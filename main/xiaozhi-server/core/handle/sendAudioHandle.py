import json
import time
import asyncio
import opuslib_next
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core import confirmation_window
from core.utils import textUtils
from core.utils.util import audio_to_data
from core.providers.tts.dto.dto import SentenceType
from core.utils.audioRateController import AudioRateController

TAG = __name__
AUDIO_FRAME_DURATION = 60  # audio frame duration (ms), aligned with Opus 60ms frames
PRE_BUFFER_COUNT = 2  # frames not queued when sending the first segment, so flow control delay does not raise the first-frame arrival time


async def sendAudioMessage(conn: "ConnectionHandler", sentenceType, audios, text, sentence_id=None):
    # skip leftover audio of an old sentence
    if sentence_id is not None and sentence_id != conn.sentence_id:
        return

    # Real reply audio, a closing tone, or an action chirp. The thinking loop
    # is the one task that must not stop itself mid-send. Anyone else drops
    # unsent modem packets before queueing this audio, and does not send tts
    # stop — that would end the turn and the board would drop the reply.
    if asyncio.current_task() is not getattr(conn, "_feedback_task", None):
        from core.utils.tones import stop_turn_feedback

        await stop_turn_feedback(conn)

    if conn.tts.tts_audio_first_sentence:
        conn.logger.bind(tag=TAG).info(f"Sending the first speech segment: {text}")
        conn.tts.tts_audio_first_sentence = False

    if sentenceType is not SentenceType.LAST:
        confirmation_window.note_spoken(text)  # a question: the next short answer gets an easier voice check

    if sentenceType == SentenceType.FIRST:
        # later messages of the same sentence join the flow-control queue; otherwise send immediately
        from core.utils import audio_device

        if (
            audio_device.is_board(conn)  # the Mac app speaks from this text event, so it must never be delayed or dropped
            and hasattr(conn, "audio_rate_controller")
            and conn.audio_rate_controller
            and getattr(conn, "audio_flow_control", {}).get("sentence_id")
            == conn.sentence_id
        ):
            conn.audio_rate_controller.add_message(
                lambda: send_tts_message(conn, "sentence_start", text)
            )
        else:
            # new sentence or flow controller not initialized: send immediately
            await send_tts_message(conn, "sentence_start", text)

    await sendAudio(conn, audios)
    # send the sentence-start message
    if sentenceType is not SentenceType.MIDDLE:
        conn.logger.bind(tag=TAG).info(f"Sending audio message: {sentenceType}, {text}")

    # send the end message (if this is the last text)
    # a call must keep the speaking state
    if not conn.calling and sentenceType == SentenceType.LAST:
        await send_tts_message(conn, "stop", None)
        if conn.close_after_chat:
            await conn.close()


async def _wait_for_audio_completion(conn: "ConnectionHandler"):
    """
    Wait for the audio queue to drain and the pre-buffered packets to finish playing

    Args:
        conn: connection object
    """
    if hasattr(conn, "audio_rate_controller") and conn.audio_rate_controller:
        rate_controller = conn.audio_rate_controller
        send_delay_ms = conn.config.get("tts_audio_send_delay", 0)
        conn.logger.bind(tag=TAG).debug(
            f"Waiting for audio sending to finish, {len(rate_controller.queue)} packets still in the queue"
        )
        await rate_controller.queue_empty_event.wait()

        if send_delay_ms > 0:
            # custom pacing: wait for the last frame to finish playing on the client
            playback_time = 2 * rate_controller.interval_ms / 1000.0
        else:
            # default pacing: the first N pre-buffered frames were sent directly; wait for them to finish playing
            playback_time = (PRE_BUFFER_COUNT + 2) * rate_controller.interval_ms / 1000.0
        await asyncio.sleep(playback_time)

        conn.logger.bind(tag=TAG).debug("Audio sending finished")


async def _send_to_mqtt_gateway(
    conn: "ConnectionHandler", opus_packet, timestamp, sequence
):
    """
    Send an opus packet with a 16-byte header to mqtt_gateway and cache the audio for AEC processing
    Args:
        conn: connection object
        opus_packet: opus packet
        timestamp: timestamp
        sequence: sequence number
    """
    # if server-side AEC is enabled, cache the PCM data for later AEC processing
    if conn.client_aec and timestamp > 0:
        if not hasattr(conn, "aec_audio_cache"):
            conn.aec_audio_cache = {}
            conn.aec_audio_cache_time = {}
            conn._send_opus_decoder = opuslib_next.Decoder(16000, 1)
        # decode the opus to PCM and cache it
        pcm_data = conn._send_opus_decoder.decode(bytes(opus_packet), 960)
        conn.aec_audio_cache[timestamp] = bytes(pcm_data)
        conn.aec_audio_cache_time[timestamp] = time.time()

    # add the 16-byte header to the opus packet
    header = bytearray(16)
    header[0] = 1  # type
    header[2:4] = len(opus_packet).to_bytes(2, "big")  # payload length
    header[4:8] = sequence.to_bytes(4, "big")  # sequence
    header[8:12] = timestamp.to_bytes(4, "big")  # timestamp
    header[12:16] = len(opus_packet).to_bytes(4, "big")  # opus length

    # send the complete packet including the header
    complete_packet = bytes(header) + opus_packet
    await conn.websocket.send(complete_packet)


async def sendAudio(
    conn: "ConnectionHandler", audios, frame_duration=AUDIO_FRAME_DURATION
):
    """
    Send audio packets with precise flow control through AudioRateController

    Args:
        conn: connection object
        audios: a single opus packet (bytes) or a list of opus packets
        frame_duration: frame duration (ms), defaults to the global constant AUDIO_FRAME_DURATION
    """
    if audios is None or len(audios) == 0:
        return

    send_delay_ms = conn.config.get("tts_audio_send_delay", 0)
    is_single_packet = isinstance(audios, bytes)

    # initialize or get the RateController
    rate_controller, flow_control = _get_or_create_rate_controller(
        conn, frame_duration, is_single_packet, send_delay_ms
    )

    # normalize to a list
    audio_list = [audios] if is_single_packet else audios

    # send the audio packets
    await _send_audio_with_rate_control(
        conn, audio_list, rate_controller, flow_control, send_delay_ms
    )


def _get_or_create_rate_controller(
    conn: "ConnectionHandler", frame_duration, is_single_packet, send_delay_ms=0
):
    """
    Get or create the RateController and flow_control

    Args:
        conn: connection object
        frame_duration: frame duration
        is_single_packet: whether single-packet mode (True: TTS streaming single packet, False: batch packets)
        send_delay_ms: custom send interval (ms).

    Returns:
        (rate_controller, flow_control)
    """
    # check whether the controller needs to be reset
    need_reset = False

    if not hasattr(conn, "audio_rate_controller"):
        # the controller does not exist and must be created
        need_reset = True
    else:
        rate_controller = conn.audio_rate_controller

        # the background send task has stopped and needs a reset
        if (
            not rate_controller.pending_send_task
            or rate_controller.pending_send_task.done()
        ):
            need_reset = True
        # when sentence_id changes, a reset is needed
        elif (
            getattr(conn, "audio_flow_control", {}).get("sentence_id")
            != conn.sentence_id
        ):
            need_reset = True

    if need_reset:
        # create or get the rate_controller
        if not hasattr(conn, "audio_rate_controller"):
            conn.audio_rate_controller = AudioRateController(
                frame_duration, send_delay=send_delay_ms
            )
        else:
            conn.audio_rate_controller.reset()

        # initialize flow_control
        conn.audio_flow_control = {
            "packet_count": 0,
            "sequence": 0,
            "sentence_id": conn.sentence_id,
        }

        # start the background send loop
        _start_background_sender(
            conn, conn.audio_rate_controller, conn.audio_flow_control
        )

    return conn.audio_rate_controller, conn.audio_flow_control


def _start_background_sender(conn: "ConnectionHandler", rate_controller, flow_control):
    """
    Start the background send loop task

    Args:
        conn: connection object
        rate_controller: rate controller
        flow_control: flow control state
    """

    async def send_callback(packet):
        # check whether it should abort
        if conn.client_abort:
            raise asyncio.CancelledError("Client aborted")

        conn.last_activity_time = time.time() * 1000
        await _do_send_audio(conn, packet, flow_control)

    # start the background loop with start_sending
    rate_controller.start_sending(send_callback)


async def _send_audio_with_rate_control(
    conn: "ConnectionHandler", audio_list, rate_controller, flow_control, send_delay_ms
):
    """
    Send audio packets with the rate_controller

    Args:
        conn: connection object
        audio_list: list of audio packets
        rate_controller: rate controller
        flow_control: flow control state
        send_delay_ms: custom send interval (ms).
    """
    for packet in audio_list:
        if conn.client_abort:
            return

        conn.last_activity_time = time.time() * 1000

        if send_delay_ms > 0:
            # custom pacing mode: all packets are queued and sent at send_delay (pre-buffering off)
            rate_controller.add_audio(packet)
        else:
            # default pacing mode (60ms/packet): the first N packets are pre-buffered and sent directly; later ones are queued and sent by the background task at interval_ms
            if flow_control["packet_count"] < PRE_BUFFER_COUNT:
                await _do_send_audio(conn, packet, flow_control)
            else:
                rate_controller.add_audio(packet)


async def _do_send_audio(conn: "ConnectionHandler", opus_packet, flow_control):
    """
    Perform the actual audio sending
    """
    packet_index = flow_control.get("packet_count", 0)
    sequence = flow_control.get("sequence", 0)

    if conn.conn_from_mqtt_gateway:
        # compute the timestamp (based on the playback position)
        start_time = time.time()
        timestamp = int(start_time * 1000) % (2**32)
        await _send_to_mqtt_gateway(conn, opus_packet, timestamp, sequence)
    else:
        # send the opus packet directly
        await conn.websocket.send(opus_packet)

    # update the flow control state
    flow_control["packet_count"] = packet_index + 1
    flow_control["sequence"] = sequence + 1


async def send_tts_message(conn: "ConnectionHandler", state, text=None):
    """Send a TTS status message"""
    if text is None and state == "sentence_start":
        return
    message = {"type": "tts", "state": state, "session_id": conn.session_id}
    if state in ("start", "stop"):
        from core.utils import audio_device

        audio_device.set_board_state(conn, "speaking" if state == "start" else "idle")
    if text is not None:
        message["text"] = textUtils.check_emoji(text)

    # TTS playback finished
    if state == "stop":
        # save the current sentence_id to decide later whether this is still the current turn
        current_sentence_id = conn.sentence_id
        # play the notification sound
        tts_notify = conn.config.get("enable_stop_tts_notify", False)
        if tts_notify:
            stop_tts_notify_voice = conn.config.get(
                "stop_tts_notify_voice", "config/assets/tts_notify.mp3"
            )
            audios = await audio_to_data(stop_tts_notify_voice, is_opus=True)
            await sendAudio(conn, audios)
        # wait for all audio packets to be sent
        await _wait_for_audio_completion(conn)

        # check whether this is the current turn
        if current_sentence_id != conn.sentence_id:
            return

        # stop the audio send loop (only when the flow controller has been initialized)
        if hasattr(conn, "audio_rate_controller") and conn.audio_rate_controller:
            conn.audio_rate_controller.stop_sending()
        conn.clearSpeakStatus()

    # send the message to the client
    await conn.websocket.send(json.dumps(message))


async def send_stt_message(conn: "ConnectionHandler", text):
    """Send an STT status message"""
    end_prompt_str = conn.config.get("end_prompt", {}).get("prompt")
    if end_prompt_str and end_prompt_str == text:
        await send_tts_message(conn, "start")
        return

    # parse the JSON format and extract the actual user speech
    display_text = text
    try:
        # try to parse the JSON format
        if text.strip().startswith("{") and text.strip().endswith("}"):
            parsed_data = json.loads(text)
            if isinstance(parsed_data, dict) and "content" in parsed_data:
                # if it is JSON containing speaker information, show only the content part
                display_text = parsed_data["content"]
                # save the speaker information on the conn object
                if "speaker" in parsed_data:
                    conn.current_speaker = parsed_data["speaker"]
    except (json.JSONDecodeError, TypeError):
        # if it is not JSON, use the original text
        display_text = text
    stt_text = textUtils.get_string_no_punctuation_or_emoji(display_text)
    await conn.websocket.send(
        json.dumps({"type": "stt", "text": stt_text, "session_id": conn.session_id})
    )
    # Feedback already sent "start" so the ack could play. A second start makes
    # the board enter speaking again and reset its decoder, which drops the ack.
    if not getattr(conn, "_feedback_turn_open", False):
        await send_tts_message(conn, "start")
    # after the start message the client enters the speaking state; sync the server state
    conn.client_is_speaking = True


async def send_display_message(conn: "ConnectionHandler", text):
    """Send a plain display message"""
    message = {
        "type": "stt",
        "text": text,
        "session_id": conn.session_id
    }
    await conn.websocket.send(json.dumps(message))

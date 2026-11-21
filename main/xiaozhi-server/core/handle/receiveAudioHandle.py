import time
import json
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.utils.util import audio_to_data
from core.handle.abortHandle import handleAbortMessage
from core.handle.intentHandler import handle_user_intent
from core.utils.output_counter import check_device_output_limit
from core.handle.sendAudioHandle import send_stt_message, SentenceType

TAG = __name__


async def handleAudioMessage(conn: "ConnectionHandler", pcm_frame):
    from core.utils import audio_device

    if audio_device.board_muted(conn):
        return  # "This Mac" is selected in the vb menu
    # whether anyone is speaking in the current segment
    have_voice = conn.vad.is_vad(conn, pcm_frame)
    if have_voice and not conn.client_is_speaking:
        audio_device.set_board_state(conn, "listening")
    # if the device was just woken up, briefly ignore VAD detection
    if hasattr(conn, "just_woken_up") and conn.just_woken_up:
        have_voice = False
        # set a short delay, then resume VAD detection
        if not hasattr(conn, "vad_resume_task") or conn.vad_resume_task.done():
            conn.vad_resume_task = asyncio.create_task(resume_vad_detection(conn))
        return
    # server-side AEC needs to trigger interruption in real time
    if conn.client_aec and have_voice:
        if conn.client_is_speaking and conn.client_listen_mode != "manual":
            await handleAbortMessage(conn)
    # detect long idle time on the device, used to say goodbye
    await no_voice_close_connect(conn, have_voice)
    # receive audio
    await conn.asr.receive_audio(conn, pcm_frame, have_voice)


async def resume_vad_detection(conn: "ConnectionHandler"):
    # wait 2 seconds, then resume VAD detection
    await asyncio.sleep(2)
    conn.just_woken_up = False


async def startToChat(conn: "ConnectionHandler", text):
    # check whether the input is JSON (contains speaker information)
    speaker_name = None
    actual_text = text

    try:
        # try to parse JSON input
        if text.strip().startswith("{") and text.strip().endswith("}"):
            data = json.loads(text)
            if "speaker" in data and "content" in data:
                speaker_name = data["speaker"]
                actual_content = data["content"]
                conn.logger.bind(tag=TAG).info(f"Parsed speaker information: {speaker_name}")

                # keep the {"speaker":...} JSON only the first time that speaker appears, so the model addresses them by name once;
                # later turns drop to plain text, so the name does not repeat every turn and make the model keep using it
                if speaker_name not in conn.introduced_speakers:
                    conn.introduced_speakers.add(speaker_name)
                    actual_text = text
                else:
                    actual_text = actual_content
            elif "content" in data:
                # some ASR engines return {"content","language","emotion"}: pass only the recognized text to the LLM,
                # otherwise the whole JSON would enter the dialogue history
                actual_text = data["content"]
    except (json.JSONDecodeError, KeyError):
        # if parsing fails, keep using the original text
        pass

    # save the speaker information on the connection object
    if speaker_name:
        conn.current_speaker = speaker_name
    else:
        conn.current_speaker = None

    if conn.need_bind:
        await check_bind_device(conn)
        return

    # if today's output character count exceeds the limit
    if conn.max_output_size > 0:
        if check_device_output_limit(
            conn.headers.get("device-id"), conn.max_output_size
        ):
            await max_out_size(conn)
            return

    # in manual mode, do not interrupt content that is playing
    if conn.client_is_speaking and conn.client_listen_mode != "manual":
        await handleAbortMessage(conn)

    # run intent analysis first, using the actual text content
    intent_handled = await handle_user_intent(conn, actual_text)

    if intent_handled:
        # if the intent was already handled, do not chat any more
        return

    # intent not handled, continue the regular chat flow, using the actual text content
    await send_stt_message(conn, actual_text)

    # prepare to start a new session
    conn.client_abort = False

    conn.executor.submit(conn.chat, actual_text)


async def no_voice_close_connect(conn: "ConnectionHandler", have_voice):
    if have_voice:
        conn.last_activity_time = time.time() * 1000
        return
    # only check for timeout when the timestamp has been initialized
    if conn.last_activity_time > 0.0:
        no_voice_time = time.time() * 1000 - conn.last_activity_time
        close_connection_no_voice_time = int(
            conn.config.get("close_connection_no_voice_time", 120)
        )
        if (
            not conn.close_after_chat
            and no_voice_time > 1000 * close_connection_no_voice_time
        ):
            conn.close_after_chat = True
            conn.client_abort = False
            end_prompt = conn.config.get("end_prompt", {})
            if end_prompt and end_prompt.get("enable", True) is False:
                conn.logger.bind(tag=TAG).info("Ending the conversation, no closing remark needed")
                await conn.close()
                return
            prompt = end_prompt.get("prompt")
            if not prompt:
                prompt = "Please end this conversation with a warm, affectionate goodbye that starts with ```Time flies```."
            await startToChat(conn, prompt)


async def max_out_size(conn: "ConnectionHandler"):
    # play the prompt for exceeding the maximum output length
    conn.client_abort = False
    text = "Sorry, I have something to take care of right now. Let's talk again at this time tomorrow, it's a date! See you tomorrow, bye!"
    await send_stt_message(conn, text)
    file_path = "config/assets/max_output_size.wav"
    opus_packets = await audio_to_data(file_path)
    conn.tts.tts_audio_queue.put((SentenceType.LAST, opus_packets, text))
    conn.close_after_chat = True


async def check_bind_device(conn: "ConnectionHandler"):
    if conn.bind_code:
        # make sure bind_code is 6 digits
        if len(conn.bind_code) != 6:
            conn.logger.bind(tag=TAG).error(f"Invalid bind code format: {conn.bind_code}")
            text = "The bind code format is wrong, please check the configuration."
            await send_stt_message(conn, text)
            return

        text = f"Please log in to the control panel, enter {conn.bind_code}, and bind the device."
        await send_stt_message(conn, text)

        # play the notification sound
        music_path = "config/assets/bind_code.wav"
        opus_packets = await audio_to_data(music_path)
        conn.tts.tts_audio_queue.put((SentenceType.FIRST, opus_packets, text))

        # play the digits one by one
        for i in range(6):  # make sure only 6 digits are played
            try:
                digit = conn.bind_code[i]
                num_path = f"config/assets/bind_code/{digit}.wav"
                num_packets = await audio_to_data(num_path)
                conn.tts.tts_audio_queue.put((SentenceType.MIDDLE, num_packets, None))
            except Exception as e:
                conn.logger.bind(tag=TAG).error(f"Failed to play the digit audio: {e}")
                continue
        conn.tts.tts_audio_queue.put((SentenceType.LAST, [], None))
    else:
        # play the unbound prompt
        conn.client_abort = False
        text = f"No version information was found for this device. Please configure the OTA address correctly and rebuild the firmware."
        await send_stt_message(conn, text)
        music_path = "config/assets/bind_not_found.wav"
        opus_packets = await audio_to_data(music_path)
        conn.tts.tts_audio_queue.put((SentenceType.LAST, opus_packets, text))

import re
import os
import json
import copy
import wave
import socket
import sys
import asyncio
import threading
import requests
import subprocess
import numpy as np
import opuslib_next
from io import BytesIO
from contextlib import contextmanager
from core.utils import p3
from pydub import AudioSegment
from typing import Callable, Any

TAG = __name__


# Work around the stripped-down ffmpeg/ffprobe PATH placeholders injected by IDEs (Trae CN etc.): during the call,
# prepend the bin directory next to the Python interpreter to PATH so pydub.utils.which() finds
# the full version shipped with the conda env first, then restore the original PATH afterwards so the global state after import is not polluted.
# Set the environment variable XIAOZHI_PYDUB_OVERRIDE_FFMPEG=0 to turn this off.
#
# Reference count + lock: when several threads enter and leave the with block concurrently, the original PATH is recorded
# and rewritten only by the first one to enter, and restored only when ref_count drops to zero. This way finally does not get out of step
# and leave the bin prefix behind permanently, and the try/finally contract is not overwritten by other threads.
_ffmpeg_path_lock = threading.Lock()
_ffmpeg_ref_count = 0
_ffmpeg_original_path: str | None = None


@contextmanager
def _ffmpeg_path_override():
    if os.environ.get("XIAOZHI_PYDUB_OVERRIDE_FFMPEG", "1") != "1":
        yield
        return
    bin_dir = os.path.dirname(sys.executable)
    ffprobe = os.path.join(bin_dir, "ffprobe")
    ffmpeg = os.path.join(bin_dir, "ffmpeg")
    if not (os.path.isfile(ffprobe) or os.path.isfile(ffmpeg)):
        yield
        return

    global _ffmpeg_ref_count, _ffmpeg_original_path
    with _ffmpeg_path_lock:
        if _ffmpeg_ref_count == 0:
            _ffmpeg_original_path = os.environ.get("PATH", "")
            os.environ["PATH"] = bin_dir + os.pathsep + _ffmpeg_original_path
        _ffmpeg_ref_count += 1
    try:
        yield
    finally:
        with _ffmpeg_path_lock:
            _ffmpeg_ref_count -= 1
            if _ffmpeg_ref_count == 0 and _ffmpeg_original_path is not None:
                os.environ["PATH"] = _ffmpeg_original_path
                _ffmpeg_original_path = None


# Delegates to util.ffmpeg_path_override so callers can use it in one line after importing util
ffmpeg_path_override = _ffmpeg_path_override


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Connect to Google's DNS servers
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception as e:
        return "127.0.0.1"


def is_private_ip(ip_addr):
    """
    Check if an IP address is a private IP address (compatible with IPv4 and IPv6).

    @param {string} ip_addr - The IP address to check.
    @return {bool} True if the IP address is private, False otherwise.
    """
    try:
        # Validate IPv4 or IPv6 address format
        if not re.match(
            r"^(\d{1,3}\.){3}\d{1,3}$|^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$", ip_addr
        ):
            return False  # Invalid IP address format

        # IPv4 private address ranges
        if "." in ip_addr:  # IPv4 address
            ip_parts = list(map(int, ip_addr.split(".")))
            if ip_parts[0] == 10:
                return True  # 10.0.0.0/8 range
            elif ip_parts[0] == 172 and 16 <= ip_parts[1] <= 31:
                return True  # 172.16.0.0/12 range
            elif ip_parts[0] == 192 and ip_parts[1] == 168:
                return True  # 192.168.0.0/16 range
            elif ip_addr == "127.0.0.1":
                return True  # Loopback address
            elif ip_parts[0] == 169 and ip_parts[1] == 254:
                return True  # Link-local address 169.254.0.0/16
            else:
                return False  # Not a private IPv4 address
        else:  # IPv6 address
            ip_addr = ip_addr.lower()
            if ip_addr.startswith("fc00:") or ip_addr.startswith("fd00:"):
                return True  # Unique Local Addresses (FC00::/7)
            elif ip_addr == "::1":
                return True  # Loopback address
            elif ip_addr.startswith("fe80:"):
                return True  # Link-local unicast addresses (FE80::/10)
            else:
                return False  # Not a private IPv6 address

    except (ValueError, IndexError):
        return False  # IP address format error or insufficient segments


def get_ip_info(ip_addr, logger):
    try:
        # import the global cache manager
        from core.utils.cache.manager import cache_manager, CacheType

        # try the cache first
        cached_ip_info = cache_manager.get(CacheType.IP_INFO, ip_addr)
        if cached_ip_info is not None:
            return cached_ip_info

        # cache miss, call the API
        if is_private_ip(ip_addr):
            ip_addr = ""
        url = f"http://ip-api.com/json/{ip_addr}"
        resp = requests.get(url, timeout=5).json()
        ip_info = {"city": resp.get("city")}

        # store in the cache
        cache_manager.set(CacheType.IP_INFO, ip_addr, ip_info)
        return ip_info
    except Exception as e:
        logger.bind(tag=TAG).error(f"Error getting client ip info: {e}")
        return {}


def write_json_file(file_path, data):
    """Write data to a JSON file"""
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def remove_punctuation_and_length(text):
    # Unicode ranges of full-width and half-width symbols
    full_width_punctuations = (
        "！＂＃＄％＆＇（）＊＋，－。／：；＜＝＞？＠［＼］＾＿｀｛｜｝～"
    )
    half_width_punctuations = r'!"#$%&\'()*+,-./:;<=>?@[\]^_`{|}~'
    space = " "  # half-width space
    full_width_space = "　"  # full-width space

    # remove full-width and half-width symbols and spaces
    result = "".join(
        [
            char
            for char in text
            if char not in full_width_punctuations
            and char not in half_width_punctuations
            and char not in space
            and char not in full_width_space
        ]
    )

    if result == "Yeah":
        return 0, ""
    return len(result), result


def check_model_key(modelType, modelKey):
    if "your" in modelKey.lower():
        return f"Configuration error: the API key of {modelType} is not set, current value: {modelKey}"
    return None


def parse_string_to_list(value, separator=";"):
    """
    Convert the input value to a list
    Args:
        value: input value, can be None, a string or a list
        separator: separator, semicolon by default
    Returns:
        list: the processed list
    """
    if value is None or value == "":
        return []
    elif isinstance(value, str):
        return [item.strip() for item in value.split(separator) if item.strip()]
    elif isinstance(value, list):
        return value
    return []


def check_ffmpeg_installed() -> bool:
    """
    Check whether ffmpeg is correctly installed and executable in the current environment.

    Returns:
        bool: True if ffmpeg works; otherwise a ValueError is raised.

    Raises:
        ValueError: raised with a detailed message when ffmpeg is missing or a dependency is absent.
    """
    try:
        # try running the ffmpeg command
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,  # a non-zero exit code raises CalledProcessError
        )

        output = (result.stdout + result.stderr).lower()
        if "ffmpeg version" in output:
            return True

        # if no version information is detected, treat it as an error too
        raise ValueError("No valid ffmpeg version output was detected.")

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        # extract the error output
        stderr_output = ""
        if isinstance(e, subprocess.CalledProcessError):
            stderr_output = (e.stderr or "").strip()
        else:
            stderr_output = str(e).strip()

        # build the base error message
        error_msg = [
            "❌ ffmpeg cannot run properly.\n",
            "Suggestions:",
            "1. Make sure the conda environment is activated correctly;",
            "2. See the project installation docs on how to install ffmpeg in a conda environment.\n",
        ]

        # 🎯 extra hints for specific error messages
        if "libiconv.so.2" in stderr_output:
            error_msg.append("⚠️ Missing dependency library: libiconv.so.2")
            error_msg.append("Fix: run in the current conda environment:")
            error_msg.append("   conda install -c conda-forge libiconv\n")
        elif (
            "no such file or directory" in stderr_output
            and "ffmpeg" in stderr_output.lower()
        ):
            error_msg.append("⚠️ The ffmpeg executable was not found on the system.")
            error_msg.append("Fix: run in the current conda environment:")
            error_msg.append("   conda install -c conda-forge ffmpeg\n")
        else:
            error_msg.append("Error details:")
            error_msg.append(stderr_output or "Unknown error.")

        # raise with detailed error information
        raise ValueError("\n".join(error_msg)) from e


def extract_json_from_string(input_string):
    """Extract the JSON part of a string"""
    pattern = r"(\{.*\})"
    match = re.search(pattern, input_string, re.DOTALL)  # add re.DOTALL
    if match:
        return match.group(1)  # return the extracted JSON string
    return None


def audio_to_data_stream(
    audio_file_path, is_opus=True, callback: Callable[[Any], Any] = None, sample_rate=16000, opus_encoder=None
) -> None:
    with _ffmpeg_path_override():
        _audio_to_data_stream_inner(
            audio_file_path, is_opus, callback, sample_rate, opus_encoder
        )


def _audio_to_data_stream_inner(
    audio_file_path, is_opus, callback, sample_rate, opus_encoder
):
    file_type = os.path.splitext(audio_file_path)[1]
    if file_type:
        file_type = file_type.lstrip(".")
    # -nostdin: stop ffmpeg from blocking on stdin
    audio = AudioSegment.from_file(
        audio_file_path, format=file_type, parameters=["-nostdin"]
    )
    audio = audio.set_channels(1).set_frame_rate(sample_rate).set_sample_width(2)
    pcm_to_data_stream(audio.raw_data, is_opus, callback, sample_rate, opus_encoder)


async def audio_to_data(
    audio_file_path: str, is_opus: bool = True, use_cache: bool = True
) -> list[bytes]:
    """
    Convert an audio file into a list of Opus/PCM encoded frames
    Args:
        audio_file_path: audio file path
        is_opus: whether to Opus-encode
        use_cache: whether to use the cache
    """
    from core.utils.cache.manager import cache_manager
    from core.utils.cache.config import CacheType

    # the cache key includes mtime: when a file is overwritten in place the old cache is invalidated automatically (e.g. a wake-word reply re-synthesized), avoiding stale audio
    try:
        mtime = os.path.getmtime(audio_file_path)
        cache_key = f"{audio_file_path}:{mtime}:{is_opus}"
    except OSError:
        cache_key = f"{audio_file_path}:{is_opus}"

    # try to get the result from the cache
    if use_cache:
        cached_result = cache_manager.get(CacheType.AUDIO_DATA, cache_key)
        if cached_result is not None:
            return cached_result

    def _sync_audio_to_data():
        # get the file extension
        file_type = os.path.splitext(audio_file_path)[1]
        if file_type:
            file_type = file_type.lstrip(".")
        # read the audio file; -nostdin: do not read from standard input, otherwise FFmpeg blocks
        with _ffmpeg_path_override():
            audio = AudioSegment.from_file(
                audio_file_path, format=file_type, parameters=["-nostdin"]
            )

        # convert to mono / 16kHz / 16-bit little-endian (to match the encoder)
        audio = audio.set_channels(1).set_frame_rate(16000).set_sample_width(2)

        # get the raw PCM data (16-bit little-endian)
        raw_data = audio.raw_data

        # initialize the Opus encoder
        encoder = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_AUDIO)

        # encoding parameters
        frame_duration = 60  # 60ms per frame
        frame_size = int(16000 * frame_duration / 1000)  # 960 samples/frame

        datas = []
        # process all audio data frame by frame (the last frame may be zero-padded)
        for i in range(0, len(raw_data), frame_size * 2):  # 16bit=2bytes/sample
            # get the binary data of the current frame
            chunk = raw_data[i : i + frame_size * 2]

            # if the last frame is short, pad with zeros
            if len(chunk) < frame_size * 2:
                chunk += b"\x00" * (frame_size * 2 - len(chunk))

            if is_opus:
                # convert to a numpy array
                np_frame = np.frombuffer(chunk, dtype=np.int16)
                # encode the Opus data
                frame_data = encoder.encode(np_frame.tobytes(), frame_size)
            else:
                frame_data = chunk if isinstance(chunk, bytes) else bytes(chunk)

            datas.append(frame_data)

        return datas

    loop = asyncio.get_running_loop()
    # run the synchronous audio processing in a separate thread
    result = await loop.run_in_executor(None, _sync_audio_to_data)

    # store the result in the cache with the TTL defined in the configuration (10 minutes)
    if use_cache:
        cache_manager.set(CacheType.AUDIO_DATA, cache_key, result)

    return result


def audio_bytes_to_data_stream(
    audio_bytes, file_type, is_opus, callback: Callable[[Any], Any], sample_rate=16000, opus_encoder=None
) -> None:
    """
    Convert audio binary data directly to opus/pcm data; supports wav, mp3, p3
    """
    if file_type == "p3":
        # decode p3 directly
        return p3.decode_opus_from_bytes_stream(audio_bytes, callback)
    else:
        # other formats use pydub
        with _ffmpeg_path_override():
            audio = AudioSegment.from_file(
                BytesIO(audio_bytes), format=file_type, parameters=["-nostdin"]
            )
        audio = audio.set_channels(1).set_frame_rate(sample_rate).set_sample_width(2)
        raw_data = audio.raw_data
        pcm_to_data_stream(raw_data, is_opus, callback, sample_rate, opus_encoder)


def pcm_to_data_stream(raw_data, is_opus=True, callback: Callable[[Any], Any] = None, sample_rate=16000, opus_encoder=None):
    """
    Encode a PCM stream into Opus, or output PCM directly

    Args:
        raw_data: raw PCM data
        is_opus: whether to encode as Opus
        callback: callback function
        sample_rate: sample rate
        opus_encoder: OpusEncoderUtils object (recommended, to keep the encoder state continuous)
    """
    using_temp_encoder = False
    if is_opus and opus_encoder is None:
        encoder = opuslib_next.Encoder(sample_rate, 1, opuslib_next.APPLICATION_AUDIO)
        using_temp_encoder = True

    # encoding parameters
    frame_duration = 60  # 60ms per frame
    frame_size = int(sample_rate * frame_duration / 1000)  # samples/frame

    # process all audio data frame by frame (the last frame may be zero-padded)
    for i in range(0, len(raw_data), frame_size * 2):  # 16bit=2bytes/sample
        # get the binary data of the current frame
        chunk = raw_data[i : i + frame_size * 2]

        # if the last frame is short, pad with zeros
        if len(chunk) < frame_size * 2:
            chunk += b"\x00" * (frame_size * 2 - len(chunk))

        if is_opus:
            if using_temp_encoder:
                # use a temporary encoder (only for standalone audio)
                np_frame = np.frombuffer(chunk, dtype=np.int16)
                frame_data = encoder.encode(np_frame.tobytes(), frame_size)
                callback(frame_data)
            else:
                # use an external encoder (TTS streaming, keeps state continuous)
                is_last = (i + frame_size * 2 >= len(raw_data))
                opus_encoder.encode_pcm_to_opus_stream(chunk, end_of_stream=is_last, callback=callback)
        else:
            # PCM mode, output directly
            frame_data = chunk if isinstance(chunk, bytes) else bytes(chunk)
            callback(frame_data)


def opus_datas_to_wav_bytes(opus_datas, sample_rate=16000, channels=1):
    """
    Decode a list of opus frames into a wav byte stream
    """
    decoder = opuslib_next.Decoder(sample_rate, channels)
    try:
        pcm_datas = []

        frame_duration = 60  # ms
        frame_size = int(sample_rate * frame_duration / 1000)  # 960

        for opus_frame in opus_datas:
            # decode to PCM (returns bytes, 2 bytes per sample)
            pcm = decoder.decode(opus_frame, frame_size)
            pcm_datas.append(pcm)

        pcm_bytes = b"".join(pcm_datas)

        # write the wav byte stream
        wav_buffer = BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)  # 16bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)
        return wav_buffer.getvalue()
    finally:
        if decoder is not None:
            try:
                del decoder
            except Exception:
                pass


def audio_file_to_wav_bytes(audio_file_path, sample_rate=16000):
    """Convert an audio file directly to a wav byte stream at the target sample rate, without Opus encode/decode, avoiding loss"""
    file_type = os.path.splitext(audio_file_path)[1]
    if file_type:
        file_type = file_type.lstrip(".")
    with _ffmpeg_path_override():
        audio = AudioSegment.from_file(
            audio_file_path, format=file_type, parameters=["-nostdin"]
        )
    audio = audio.set_channels(1).set_frame_rate(sample_rate).set_sample_width(2)
    buffer = BytesIO()
    audio.export(buffer, format="wav")
    return buffer.getvalue()


def check_vad_update(before_config, new_config):
    if (
        new_config.get("selected_module") is None
        or new_config["selected_module"].get("VAD") is None
    ):
        return False
    update_vad = False
    current_vad_module = before_config["selected_module"]["VAD"]
    new_vad_module = new_config["selected_module"]["VAD"]
    current_vad_type = (
        current_vad_module
        if "type" not in before_config["VAD"][current_vad_module]
        else before_config["VAD"][current_vad_module]["type"]
    )
    new_vad_type = (
        new_vad_module
        if "type" not in new_config["VAD"][new_vad_module]
        else new_config["VAD"][new_vad_module]["type"]
    )
    update_vad = current_vad_type != new_vad_type
    return update_vad


def check_asr_update(before_config, new_config):
    if (
        new_config.get("selected_module") is None
        or new_config["selected_module"].get("ASR") is None
    ):
        return False
    update_asr = False
    current_asr_module = before_config["selected_module"]["ASR"]
    new_asr_module = new_config["selected_module"]["ASR"]

    # if the module names differ, an update is needed
    if current_asr_module != new_asr_module:
        return True

    # if the module names are the same, compare the types
    current_asr_type = (
        current_asr_module
        if "type" not in before_config["ASR"][current_asr_module]
        else before_config["ASR"][current_asr_module]["type"]
    )
    new_asr_type = (
        new_asr_module
        if "type" not in new_config["ASR"][new_asr_module]
        else new_config["ASR"][new_asr_module]["type"]
    )
    update_asr = current_asr_type != new_asr_type
    return update_asr


def filter_sensitive_info(config: dict) -> dict:
    """
    Filter sensitive information out of the configuration
    Args:
        config: original configuration dict
    Returns:
        the filtered configuration dict
    """
    sensitive_keys = [
        "api_key",
        "personal_access_token",
        "access_token",
        "token",
        "secret",
        "access_key_secret",
        "secret_key",
    ]

    def _filter_dict(d: dict) -> dict:
        filtered = {}
        for k, v in d.items():
            if any(sensitive in k.lower() for sensitive in sensitive_keys):
                filtered[k] = "***"
            elif isinstance(v, dict):
                filtered[k] = _filter_dict(v)
            elif isinstance(v, list):
                filtered[k] = [_filter_dict(i) if isinstance(i, dict) else i for i in v]
            elif isinstance(v, str):
                try:
                    json_data = json.loads(v)
                    if isinstance(json_data, dict):
                        filtered[k] = json.dumps(
                            _filter_dict(json_data), ensure_ascii=False
                        )
                    else:
                        filtered[k] = v
                except (json.JSONDecodeError, TypeError):
                    filtered[k] = v
            else:
                filtered[k] = v
        return filtered

    return _filter_dict(copy.deepcopy(config))


def get_vision_url(config: dict) -> str:
    """Get the vision URL

    Args:
        config: configuration dict

    Returns:
        str: vision URL
    """
    server_config = config["server"]
    vision_explain = server_config.get("vision_explain", "")
    if "your" in vision_explain:
        local_ip = get_local_ip()
        port = int(server_config.get("http_port", 8003))
        vision_explain = f"http://{local_ip}:{port}/mcp/vision/explain"
    return vision_explain


def is_valid_image_file(file_data: bytes) -> bool:
    """
    Check whether file data is a valid image format

    Args:
        file_data: binary data of the file

    Returns:
        bool: True if it is a valid image format, otherwise False
    """
    # magic numbers (file headers) of common image formats
    image_signatures = {
        b"\xff\xd8\xff": "JPEG",
        b"\x89PNG\r\n\x1a\n": "PNG",
        b"GIF87a": "GIF",
        b"GIF89a": "GIF",
        b"BM": "BMP",
        b"II*\x00": "TIFF",
        b"MM\x00*": "TIFF",
        b"RIFF": "WEBP",
    }

    # check whether the file header matches any known image format
    for signature in image_signatures:
        if file_data.startswith(signature):
            return True

    return False


def sanitize_tool_name(name: str) -> str:
    """Sanitize tool names for OpenAI compatibility."""
    # supports letters, digits, underscores and hyphens (and CJK characters)
    return re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", name)


def validate_mcp_endpoint(mcp_endpoint: str) -> bool:
    """
    Validate the MCP endpoint format

    Args:
        mcp_endpoint: MCP endpoint string

    Returns:
        bool: whether it is valid
    """
    # 1. check whether it starts with ws
    if not mcp_endpoint.startswith("ws"):
        return False

    # 2. check whether it contains key and call
    if "key" in mcp_endpoint.lower() or "call" in mcp_endpoint.lower():
        return False

    # 3. check whether it contains /mcp/
    if "/mcp/" not in mcp_endpoint:
        return False

    return True

def get_system_error_response(config: dict) -> str:
    """Get the reply used on a system error

    Args:
        config: configuration dict

    Returns:
        str: the reply used on a system error
    """
    return config.get("system_error_response", "Sorry, I'm a bit busy right now. Let's try again in a moment.")
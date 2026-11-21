import re
import json
import uuid
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.utils.dialogue import Message
from core.providers.tts.dto.dto import ContentType
from core.handle.helloHandle import checkWakeupWords
from plugins_func.register import Action, ActionResponse
from core.handle.sendAudioHandle import send_stt_message
from core.handle.reportHandle import enqueue_tool_report
from core.utils.util import remove_punctuation_and_length
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType

TAG = __name__


async def handle_user_intent(conn: "ConnectionHandler", text):
    # preprocess the input text, which may be JSON
    try:
        if text.strip().startswith("{") and text.strip().endswith("}"):
            parsed_data = json.loads(text)
            if isinstance(parsed_data, dict) and "content" in parsed_data:
                text = parsed_data["content"]  # extract content for intent analysis
                conn.current_speaker = parsed_data.get("speaker")  # keep the speaker information
    except (json.JSONDecodeError, TypeError):
        pass

    # check for an explicit exit command
    _, filtered_text = remove_punctuation_and_length(text)
    if await check_direct_exit(conn, filtered_text):
        return True

    # check whether it is a wake word
    if await checkWakeupWords(conn, filtered_text):
        return True

    if conn.intent_type == "function_call":
        # use the chat method that supports function calling, no more intent analysis
        return False
    # use the LLM for intent analysis
    intent_result = await analyze_intent_with_llm(conn, text)
    if not intent_result:
        return False
    # generate sentence_id when the session starts
    conn.sentence_id = str(uuid.uuid4().hex)
    # handle the various intents
    return await process_intent_result(conn, intent_result, text)


async def check_direct_exit(conn: "ConnectionHandler", text):
    """Check for an explicit exit command"""
    _, text = remove_punctuation_and_length(text)
    cmd_exit = conn.cmd_exit
    heard = re.sub(r"[\W_]+", "", text).lower()
    for cmd in cmd_exit:
        # compare ignoring case, spaces and punctuation: "Bye." must match "bye" and "stop listening"
        if heard == re.sub(r"[\W_]+", "", cmd).lower():
            conn.logger.bind(tag=TAG).info(f"Recognized explicit exit command: {text}")
            await send_stt_message(conn, text)
            await conn.close()
            return True
    return False


async def analyze_intent_with_llm(conn: "ConnectionHandler", text):
    """Analyze the user's intent with the LLM"""
    if not hasattr(conn, "intent") or not conn.intent:
        conn.logger.bind(tag=TAG).warning("Intent recognition service is not initialized")
        return None

    # dialogue history
    dialogue = conn.dialogue
    try:
        intent_result = await conn.intent.detect_intent(conn, dialogue.dialogue, text)
        return intent_result
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"Intent recognition failed: {str(e)}")

    return None


async def process_intent_result(
    conn: "ConnectionHandler", intent_result, original_text
):
    """Handle the intent recognition result"""
    try:
        # try to parse the result as JSON
        intent_data = json.loads(intent_result)

        # check for function_call
        if "function_call" in intent_data:
            # function_call obtained directly from intent recognition
            conn.logger.bind(tag=TAG).debug(
                f"Detected intent result in function_call format: {intent_data['function_call']['name']}"
            )
            function_name = intent_data["function_call"]["name"]
            if function_name == "continue_chat":
                return False

            if function_name == "result_for_context":
                await send_stt_message(conn, original_text)
                conn.client_abort = False

                def process_context_result():
                    conn.dialogue.put(Message(role="user", content=original_text))

                    from core.utils.current_time import get_current_time_info

                    current_time, today_date, today_weekday = (
                        get_current_time_info()
                    )

                    # build the base prompt with context
                    context_prompt = f"""Current time: {current_time}
                                        Today's date: {today_date} ({today_weekday})

                                        Answer the user's question based on the information above: {original_text}"""

                    # use an async call to avoid blocking the event loop and affecting audio playback on other devices
                    try:
                        response = asyncio.run_coroutine_threadsafe(
                            conn.intent.replyResult(context_prompt, original_text),
                            conn.loop,
                        ).result()
                    except Exception as e:
                        conn.logger.bind(tag=TAG).error(f"LLM failed to generate a reply: {e}")
                        response = None
                    if response:
                        speak_txt(conn, response)

                conn.executor.submit(process_context_result)
                return True

            function_args = {}
            if "arguments" in intent_data["function_call"]:
                function_args = intent_data["function_call"]["arguments"]
                if function_args is None:
                    function_args = {}
            # make sure the arguments are a JSON string
            if isinstance(function_args, dict):
                function_args = json.dumps(function_args)

            function_call_data = {
                "name": function_name,
                "id": str(uuid.uuid4().hex),
                "arguments": function_args,
            }

            await send_stt_message(conn, original_text)
            conn.client_abort = False

            # prepare the tool call arguments
            tool_input = {}
            if function_args:
                if isinstance(function_args, str):
                    tool_input = json.loads(function_args) if function_args else {}
                elif isinstance(function_args, dict):
                    tool_input = function_args

            # report the tool call
            enqueue_tool_report(conn, function_name, tool_input)

            # run the function call and result handling in the executor
            def process_function_call():
                conn.dialogue.put(Message(role="user", content=original_text))
                
                # tool call timeout
                tool_call_timeout = int(conn.config.get("tool_call_timeout", 30))
                # handle all tool calls with the unified tool handler
                try:
                    result = asyncio.run_coroutine_threadsafe(
                        conn.func_handler.handle_llm_function_call(
                            conn, function_call_data
                        ),
                        conn.loop,
                    ).result(timeout=tool_call_timeout)
                except Exception as e:
                    conn.logger.bind(tag=TAG).error(f"Tool call failed: {e}")
                    result = ActionResponse(
                        action=Action.ERROR, result="The tool call timed out, please try again in a moment", response="The tool call timed out, please try again in a moment"
                    )

                # report the tool call result
                if result:
                    enqueue_tool_report(conn, function_name, tool_input, str(result.result) if result.result else None, report_tool_call=False)

                    if result.action == Action.RESPONSE:  # reply to the front end directly
                        text = result.response
                        if text is not None:
                            speak_txt(conn, text)
                    elif result.action == Action.REQLLM:  # after the function call, ask the LLM to generate the reply
                        text = result.result
                        conn.dialogue.put(Message(role="tool", content=text))
                        # use an async call to avoid blocking the event loop and affecting audio playback on other devices
                        try:
                            llm_result = asyncio.run_coroutine_threadsafe(
                                conn.intent.replyResult(text, original_text),
                                conn.loop,
                            ).result()
                        except Exception as e:
                            conn.logger.bind(tag=TAG).error(f"LLM failed to generate a reply: {e}")
                            llm_result = text
                        if llm_result is None:
                            llm_result = text
                        speak_txt(conn, llm_result)
                    elif (
                        result.action == Action.NOTFOUND
                        or result.action == Action.ERROR
                    ):
                        text = result.response if result.response else result.result
                        if text is not None:
                            speak_txt(conn, text)
                    elif function_name != "play_music":
                        # For backward compatibility with original code
                        # get the latest text index
                        text = result.response
                        if text is None:
                            text = result.result
                        if text is not None:
                            speak_txt(conn, text)

            # run the function in the thread pool
            conn.executor.submit(process_function_call)
            return True
        return False
    except json.JSONDecodeError as e:
        conn.logger.bind(tag=TAG).error(f"Error while handling the intent result: {e}")
        return False


def speak_txt(conn: "ConnectionHandler", text):
    # record the text to sentence_id mapping
    conn.tts.store_tts_text(conn.sentence_id, text)

    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.FIRST,
            content_type=ContentType.ACTION,
        )
    )
    conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=text)
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.LAST,
            content_type=ContentType.ACTION,
        )
    )
    conn.dialogue.put(Message(role="assistant", content=text))

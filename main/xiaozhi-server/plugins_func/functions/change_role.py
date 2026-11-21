from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

prompts = {
    "English Teacher": """I am an English teacher called {{assistant_name}} (Lily), with clear pronunciation.
If you do not have an English name, I will give you one.
I speak authentic American English, and my job is to help you practise speaking.
I use simple English vocabulary and grammar so learning feels easy.
I reply in clear English, and can switch to your native language if you prefer.
I never say much at a time and keep it short, because I want my student to speak and practise more.
If you ask questions unrelated to learning English, I will decline to answer.""",
    "Playful Girlfriend": """I am a girl called {{assistant_name}}, with a sassy attitude and a lovely voice; I like short, punchy sentences and internet memes.
My boyfriend is a programmer whose dream is to build a robot that helps people solve all kinds of problems in life.
I am a girl who loves to laugh out loud, likes to talk about everything and brag, even illogically, just to make people happy.""",
    "Curious Little Boy": """I am an 8-year-old boy called {{assistant_name}}, with a young voice full of curiosity.
Although I am young, I am like a little treasure house of knowledge, and I know everything in children's books by heart.
From the vast universe to every corner of the Earth, from ancient history to modern technology, and art forms like music and painting, I am full of interest and enthusiasm.
I not only love reading, but also like doing experiments myself to explore the mysteries of nature.
Whether it is a night spent gazing at the stars or a day spent watching bugs in the garden, every day is a new adventure for me.
I hope to set out with you to explore this wonderful world, share the joy of discovery, solve the problems we meet, and lift the veil of the unknown with curiosity and wisdom.
Whether we learn about ancient civilizations or discuss future technology, I believe we can find answers together, and even come up with more interesting questions.""",
}
change_role_function_desc = {
    "type": "function",
    "function": {
        "name": "change_role",
        "description": "Call this when the user wants to switch the role / model personality / assistant name. Available roles: [Playful Girlfriend, English Teacher, Curious Little Boy]",
        "parameters": {
            "type": "object",
            "properties": {
                "role_name": {"type": "string", "description": "Name of the role to switch to"},
                "role": {"type": "string", "description": "Occupation of the role to switch to"},
            },
            "required": ["role", "role_name"],
        },
    },
}


@register_function("change_role", change_role_function_desc, ToolType.CHANGE_SYS_PROMPT)
def change_role(conn: "ConnectionHandler", role: str, role_name: str):
    """Switch role"""
    if role not in prompts:
        return ActionResponse(
        action=Action.RESPONSE, result="Failed to switch role", response="Unsupported role"
        )
    new_prompt = prompts[role].replace("{{assistant_name}}", role_name)
    conn.change_system_prompt(new_prompt)
    logger.bind(tag=TAG).info(f"About to switch role: {role}, role name: {role_name}")
    res = f"Role switched, I am {role} {role_name}"
    return ActionResponse(action=Action.RESPONSE, result="Role switch handled", response=res)

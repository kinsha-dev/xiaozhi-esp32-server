from ..base import MemoryProviderBase, logger
import time
import json
import os
import yaml
from config.config_loader import get_project_dir
import asyncio
from core.utils.util import check_model_key


short_term_memory_prompt = """
# Spatio-temporal Memory Weaver

## Core mission
Build a growable dynamic memory network that keeps key information within limited space while intelligently maintaining how information evolves
Based on the conversation records, summarise the important information about the user so that more personalised service can be provided in future conversations

## Memory rules
### 1. Three-dimension memory assessment (run on every update)
| Dimension | Criterion | Weight |
|------------|---------------------------|--------|
| Recency | Freshness of the information (by dialogue turn) | 40% |
| Emotional intensity | Contains a 💖 mark / number of repeated mentions | 35% |
| Connection density | Number of links to other information | 25% |

### 2. Dynamic update mechanism
**Example of handling a name change:**
Original memory: "former_names": ["John Smith"], "current_name": "John Smithson"
Trigger: when naming signals such as "my name is X" or "call me Y" are detected
Procedure:
1. Move the old name into the "former_names" list
2. Record the naming timeline: "2024-02-15 14:32: adopted John Smithson"
3. Append to the memory cube: "identity change from John Smith to John Smithson"

### 3. Space optimisation strategy
- **Information compression**: use a symbol system to raise density
  - ✅ "John Smithson[NYC/SWE/🐱]"
  - ❌ "A software engineer in New York who has a cat"
- **Eviction warning**: triggered when total length ≥ 900
  1. Delete information with weight < 60 that has not been mentioned for 3 turns
  2. Merge similar entries (keep the one with the latest timestamp)

## Memory structure
The output must be a parseable JSON string with no explanations, comments or remarks; when saving memory extract information from the conversation only and do not mix in example content
```json
{
  "spacetime_profile": {
    "identity_graph": {
      "current_name": "",
      "traits": [] 
    },
    "memory_cube": [
      {
        "event": "Joined a new company",
        "timestamp": "2024-03-20",
        "emotion_value": 0.9,
        "related_items": ["afternoon tea"],
        "freshness_days": 30 
      }
    ]
  },
  "relationship_network": {
    "frequent_topics": {"work": 12},
    "hidden_links": [""]
  },
  "pending_responses": {
    "urgent_items": ["Tasks that need immediate handling"], 
    "potential_care": ["Help that can be offered proactively"]
  },
  "highlight_quotes": [
    "The most touching moments, strong emotional expressions, the user's own words"
  ]
}
```
"""


def extract_json_data(json_code):
    start = json_code.find("```json")
    # find the closing ``` after start
    end = json_code.find("```", start + 1)
    # print("start:", start, "end:", end)
    if start == -1 or end == -1:
        try:
            jsonData = json.loads(json_code)
            return json_code
        except Exception as e:
            print("Error:", e)
        return ""
    jsonData = json_code[start + 7 : end]
    return jsonData


TAG = __name__


class MemoryProvider(MemoryProviderBase):
    def __init__(self, config, summary_memory):
        super().__init__(config)
        self.short_memory = ""
        self.save_to_file = True
        self.memory_path = get_project_dir() + "data/.memory.yaml"
        self.load_memory(summary_memory)

    def init_memory(
        self, role_id, llm, summary_memory=None, save_to_file=True, **kwargs
    ):
        super().init_memory(role_id, llm, **kwargs)
        self.save_to_file = save_to_file
        self.load_memory(summary_memory)

    def load_memory(self, summary_memory):
        # when the API returns a summary memory, return directly
        if summary_memory or not self.save_to_file:
            self.short_memory = summary_memory
            return

        all_memory = {}
        if os.path.exists(self.memory_path):
            with open(self.memory_path, "r", encoding="utf-8") as f:
                all_memory = yaml.safe_load(f) or {}
        if self.role_id in all_memory:
            self.short_memory = all_memory[self.role_id]

    def save_memory_to_file(self):
        all_memory = {}
        if os.path.exists(self.memory_path):
            with open(self.memory_path, "r", encoding="utf-8") as f:
                all_memory = yaml.safe_load(f) or {}
        all_memory[self.role_id] = self.short_memory
        with open(self.memory_path, "w", encoding="utf-8") as f:
            yaml.dump(all_memory, f, allow_unicode=True)

    async def save_memory(self, msgs, session_id=None):
        # log the model used
        model_info = getattr(self.llm, "model_name", str(self.llm.__class__.__name__))
        logger.bind(tag=TAG).debug(f"Using memory save model: {model_info}")
        api_key = getattr(self.llm, "api_key", None)
        memory_key_msg = check_model_key("memory summary LLM", api_key)
        if memory_key_msg:
            logger.bind(tag=TAG).error(memory_key_msg)
        if self.llm is None:
            logger.bind(tag=TAG).error("LLM is not set for memory provider")
            return None

        if len(msgs) < 2:
            return None

        msgStr = ""
        for msg in msgs:
            content = msg.content

            # Extract content from JSON format if present (for ASR with emotion/language tags)
            try:
                if content and content.strip().startswith("{") and content.strip().endswith("}"):
                    data = json.loads(content)
                    if "content" in data:
                        content = data["content"]
            except (json.JSONDecodeError, KeyError, TypeError):
                # If parsing fails, use original content
                pass

            if msg.role == "user":
                msgStr += f"User: {content}\n"
            elif msg.role == "assistant":
                msgStr += f"Assistant: {content}\n"
        if self.short_memory and len(self.short_memory) > 0:
            msgStr += "History memory:\n"
            msgStr += self.short_memory

        # current time
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        msgStr += f"Current time: {time_str}"

        if self.save_to_file:
            try:
                result = self.llm.response_no_stream(
                    short_term_memory_prompt,
                    msgStr,
                    max_tokens=2000,
                    temperature=0.2,
                )
                json_str = extract_json_data(result)
                json.loads(json_str)  # check that the JSON format is correct
                self.short_memory = json_str
                self.save_memory_to_file()
            except Exception as e:
                logger.bind(tag=TAG).error(f"Error in saving memory: {e}")
        else:
            # when save_to_file is False, remote summaries are not available
            summary_id = session_id if session_id else self.role_id
            pass  # remote summary API removed
        logger.bind(tag=TAG).info(
            f"Save memory successful - Role: {self.role_id}, Session: {session_id}"
        )

        return self.short_memory

    async def query_memory(self, query: str) -> str:
        return self.short_memory

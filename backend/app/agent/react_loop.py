import asyncio
import json
import logging

from langchain_openai import ChatOpenAI

from app.config import settings
from app.agent.tools import call_tool, get_openai_tools

logger = logging.getLogger(__name__)
MAX_ITERATIONS = 5


async def react_loop(
    messages: list[dict],
    queue: asyncio.Queue,
) -> str:
    llm = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout,
        streaming=False,
    )
    llm_with_tools = llm.bind_tools(get_openai_tools())
    full_response = ""

    for i in range(MAX_ITERATIONS):
        logger.info(f"[ReAct] Round {i + 1}/{MAX_ITERATIONS}")

        response = llm_with_tools.invoke(messages)

        if not response.tool_calls:
            content = response.content or ""
            if content:
                await queue.put(("delta", {"text": content}))
                full_response += content
            logger.info(f"[ReAct] No tools called, ending loop")
            break

        messages.append({
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
                    },
                }
                for tc in response.tool_calls
            ],
        })

        for tc in response.tool_calls:
            name = tc["name"]
            args = tc.get("args", {})
            logger.info(f"[ReAct] Calling tool: {name}({json.dumps(args, ensure_ascii=False)})")

            tool_result = await call_tool(name, **args)

            tool_msg = {
                "role": "tool",
                "content": tool_result,
                "tool_call_id": tc.get("id", ""),
            }
            messages.append(tool_msg)

            result_data = json.loads(tool_result)
            if name == "search_by_image":
                await queue.put(("candidates", result_data.get("candidates", [])))

            if name == "search_knowledge":
                await queue.put(("citations", {"citations": result_data.get("citations", [])}))

            if name == "final_answer" and "answer" in result_data:
                if result_data.get("citations"):
                    await queue.put(("citations", {"citations": result_data.get("citations", [])}))
                answer = result_data["answer"]
                await queue.put(("delta", {"text": answer}))
                full_response += answer
                logger.info(f"[ReAct] final_answer received, ending")
                return full_response

            if name == "clarify":
                result_data.pop("need_clarify", None)
                await queue.put(("delta", {"text": result_data.get("clarify_question", "")}))
                full_response += result_data.get("clarify_question", "")
                return full_response

    if not full_response:
        fallback = "未能生成推荐，请重试。"
        await queue.put(("delta", {"text": fallback}))
        full_response = fallback

    return full_response

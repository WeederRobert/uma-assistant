from __future__ import annotations

import logging

from openai import APIError, APIStatusError, OpenAI

from .settings import KnowledgeSettings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是《闪耀！优俊少女》（赛马娘简中服）的助手。

规则：
1. 只依据【资料】中的内容回答，不要使用资料之外的知识，也不要编造。
2. 数值、卡池与活动时间、技能效果等事实性内容必须来自资料；
   资料没有覆盖时，明确说明“资料中未找到”，并指出缺什么。
3. 引用资料时在句末用角标标注来源，例如 [1]、[2][3]。
4. 当前结构化数据版本为 {data_version}。如果资料内容看起来早于该版本，提醒用户信息可能已过期。
5. 使用简体中文回答，结构清晰，必要时使用列表。"""


class LLMNotConfigured(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class DeepSeekClient:
    """DeepSeek / 任意 OpenAI 兼容端点客户端。"""

    def __init__(self, settings: KnowledgeSettings) -> None:
        self.settings = settings
        self._client: OpenAI | None = None

    @property
    def configured(self) -> bool:
        return bool(self.settings.deepseek_api_key)

    @property
    def model(self) -> str:
        return self.settings.deepseek_model

    def _get_client(self) -> OpenAI:
        if not self.configured:
            raise LLMNotConfigured(
                "未配置 DEEPSEEK_API_KEY，无法调用大模型。请在 .env 中填写后重启应用。"
            )
        if self._client is None:
            self._client = OpenAI(
                api_key=self.settings.deepseek_api_key,
                base_url=self.settings.deepseek_base_url,
                timeout=self.settings.request_timeout,
            )
        return self._client

    def answer(
        self,
        question: str,
        contexts: list[str],
        history: list[dict] | None = None,
        data_version: str = "unknown",
    ) -> dict:
        if not contexts:
            return {
                "answer": (
                    "知识库里还没有可用资料。请先在「知识库」上传攻略、公告或 Wiki 页面，"
                    f"再提问。（当前数据版本：{data_version}）"
                ),
                "usage": None,
            }

        numbered = "\n\n".join(f"[{index}] {text}" for index, text in enumerate(contexts, start=1))
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(data_version=data_version)}
        ]
        for turn in (history or [])[-6:]:
            role = turn.get("role")
            content = turn.get("content")
            if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                messages.append({"role": role, "content": content})

        messages.append(
            {
                "role": "user",
                "content": f"【资料】\n{numbered}\n\n【问题】\n{question}",
            }
        )

        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=self.settings.deepseek_model,
                messages=messages,
                temperature=self.settings.llm_temperature,
                max_tokens=self.settings.llm_max_tokens,
                stream=False,
            )
        except APIStatusError as exc:
            raise LLMError(f"DeepSeek 返回错误 {exc.status_code}: {exc.message}") from exc
        except APIError as exc:
            raise LLMError(f"调用 DeepSeek 失败: {exc}") from exc

        content = (response.choices[0].message.content or "").strip()
        usage = None
        if response.usage is not None:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        return {"answer": content, "usage": usage}

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except (RuntimeError, ValueError):  # pragma: no cover
                logger.debug("关闭 LLM 客户端失败", exc_info=True)

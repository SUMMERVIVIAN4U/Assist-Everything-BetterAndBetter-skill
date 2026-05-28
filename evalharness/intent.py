from __future__ import annotations

from dataclasses import dataclass
import json

from .llm import MimoClient, mimo_configured


@dataclass(frozen=True)
class ConfirmationIntent:
    is_confirmation: bool
    gift_object: str = ""
    action: str = "none"
    confidence: float = 0.0
    reason: str = ""


def should_classify_confirmation(text: str, context: str) -> bool:
    if not _relationship_context(text, context):
        return False
    if any(token in text for token in ["就不能", "就是不", "就不要", "不满意", "为什么"]):
        return False
    return any(token in text for token in ["就", "定", "选", "确认", "下单", "买", "送出", "可以", "满意", "吧"])


def classify_confirmation_intent(
    text: str,
    context: str,
    *,
    client: MimoClient | None = None,
) -> ConfirmationIntent:
    if not should_classify_confirmation(text, context):
        return ConfirmationIntent(False, reason="no_confirmation_candidate")
    if client is not None or mimo_configured():
        try:
            return _classify_with_llm(text, context, client or MimoClient())
        except Exception as exc:
            fallback = _fallback_confirmation(text, context)
            return ConfirmationIntent(
                fallback.is_confirmation,
                fallback.gift_object,
                fallback.action,
                fallback.confidence,
                f"llm_failed:{exc}; fallback:{fallback.reason}",
            )
    return _fallback_confirmation(text, context)


def _classify_with_llm(text: str, context: str, client: MimoClient) -> ConfirmationIntent:
    data = client.json_chat(
        [
            {
                "role": "system",
                "content": (
                    "你只判断用户在礼物选择对话中是否确认了一个最终方案。"
                    "不要扩写，不要建议礼物，只返回 JSON。"
                    "字段：is_confirmation:boolean, gift_object:string, action:selected|sent|none, "
                    "confidence:number, reason:string。"
                    "确认包括：用户说就某个具体礼物吧、定这个、选这个、下单/确认送出。"
                    "如果用户只是提供偏好、纠错、问问题或否定，不算确认。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "conversation_context": context[-3000:],
                        "user_message": text,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        temperature=0.0,
    )
    confidence = float(data.get("confidence", 0) or 0)
    action = str(data.get("action", "none") or "none")
    gift_object = str(data.get("gift_object", "") or "").strip()
    is_confirmation = bool(data.get("is_confirmation")) and confidence >= 0.7 and bool(gift_object)
    if action not in {"selected", "sent", "none"}:
        action = "none"
    return ConfirmationIntent(
        is_confirmation=is_confirmation,
        gift_object=gift_object,
        action=action if is_confirmation else "none",
        confidence=confidence,
        reason=str(data.get("reason", "") or ""),
    )


def _fallback_confirmation(text: str, context: str) -> ConfirmationIntent:
    object_text = _extract_concrete_gift(text) or _latest_gift_from_context(context)
    if object_text and any(token in text for token in ["确认送出", "送出", "下单", "买了", "付款"]):
        return ConfirmationIntent(True, object_text, "sent", 0.72, "fallback_execution_signal")
    if object_text and _looks_final(text):
        return ConfirmationIntent(True, object_text, "selected", 0.72, "fallback_final_choice_signal")
    return ConfirmationIntent(False, object_text, "none", 0.0, "fallback_not_confirmation")


def _relationship_context(text: str, context: str) -> bool:
    haystack = text + "\n" + context
    return any(token in haystack for token in ["女朋友", "礼物", "送礼", "首饰", "手链", "项链", "耳钉", "玫瑰金"])


def _looks_final(text: str) -> bool:
    stripped = text.strip()
    if any(token in stripped for token in ["就这个", "就它", "定这个", "选这个", "按这个", "满意", "可以"]):
        return True
    return stripped.startswith("就") and stripped.endswith(("吧", "了", "就行", "好了"))


def _extract_concrete_gift(text: str) -> str:
    compact = text.strip(" 。！!，,")
    if compact.startswith("就"):
        compact = compact[1:]
    for suffix in ["吧", "了", "就行", "好了"]:
        if compact.endswith(suffix):
            compact = compact[: -len(suffix)]
    compact = compact.strip(" 。！!，,")
    if any(token in compact for token in ["手链", "项链", "耳钉", "吊坠", "香氛", "香水", "方巾", "包"]):
        return compact
    return ""


def _latest_gift_from_context(context: str) -> str:
    gifts = []
    for line in context.splitlines():
        if "推荐：" in line:
            tail = line.split("推荐：", 1)[1].strip()
            gift = tail.split("。", 1)[0].split("\n", 1)[0].strip()
            if gift:
                gifts.append(gift)
    return gifts[-1] if gifts else ""

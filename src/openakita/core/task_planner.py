"""
Task Planner — decomposes complex tasks into sub-tasks and orchestrates execution.

Used by the plan_task tool to:
1. Analyze a task via LLM and break it into sub-tasks
2. Execute sub-tasks in parallel or sequence based on dependencies
3. Collect results and return summary
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _step_id_aliases(step_id: str, *, position: int | None = None) -> tuple[str, ...]:
    raw = str(step_id or "").strip()
    aliases: list[str] = []
    if raw:
        aliases.append(raw)
        if raw.isdigit():
            aliases.append(f"step_{raw}")
        elif raw.startswith("step_") and raw[5:].isdigit():
            aliases.append(raw[5:])
    if position is not None and position > 0:
        aliases.extend([str(position), f"step_{position}"])
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def canonicalize_sub_tasks(tasks: list["SubTask"]) -> list["SubTask"]:
    """Rewrite planner step ids into the single canonical ``step_N`` form."""
    normalized: list[SubTask] = []
    alias_to_canonical: dict[str, str] = {}

    for index, task in enumerate(tasks or [], start=1):
        canonical_id = f"step_{index}"
        clone = SubTask(
            id=canonical_id,
            description=str(task.description or ""),
            required_tools=list(task.required_tools or []),
            agent_profile=str(task.agent_profile or "default"),
            depends_on=list(task.depends_on or []),
            estimated_complexity=str(task.estimated_complexity or "medium"),
            status=str(task.status or "pending"),
            result=str(task.result or ""),
        )
        normalized.append(clone)
        for alias in _step_id_aliases(task.id, position=index):
            alias_to_canonical[alias] = canonical_id

    for index, task in enumerate(normalized, start=1):
        canonical_deps: list[str] = []
        for dep in list(task.depends_on or []):
            for alias in _step_id_aliases(dep):
                mapped = alias_to_canonical.get(alias)
                if mapped:
                    canonical_deps.append(mapped)
                    break
        task.depends_on = list(dict.fromkeys(canonical_deps))
        task.id = f"step_{index}"

    return normalized


@dataclass
class SubTask:
    """A single sub-task within a larger plan."""

    id: str
    description: str
    required_tools: list[str] = field(default_factory=list)
    agent_profile: str = "default"
    depends_on: list[str] = field(default_factory=list)
    estimated_complexity: str = "medium"
    status: str = "pending"  # pending / running / completed / failed
    result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "required_tools": self.required_tools,
            "agent_profile": self.agent_profile,
            "depends_on": self.depends_on,
            "estimated_complexity": self.estimated_complexity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubTask:
        return cls(
            id=str(data.get("id", "")),
            description=data.get("description", ""),
            required_tools=data.get("required_tools", []),
            agent_profile=data.get("agent_profile", "default"),
            depends_on=data.get("depends_on", []),
            estimated_complexity=data.get("estimated_complexity", "medium"),
        )


class TaskPlanner:
    """Plans and executes sub-tasks via LLM decomposition."""

    PLANNER_SYSTEM_PROMPT = """\
You are a Task Planner. Decompose user requests into executable sub-tasks.

Rules:
1. Each sub-task must be independent and verifiable
2. Minimize dependencies — parallelize whenever possible
3. required_tools MUST be chosen from the available tools list only
4. Complexity: low (1 step), medium (2-3 steps), high (requires reasoning)
5. Maximum {max_sub_tasks} sub-tasks
6. Use canonical step ids in the form "step_1", "step_2", ...
7. Output strict JSON array only, no markdown, no explanation
8. Do not output <think> / <thinking> tags or any reasoning text
9. The first non-whitespace character of the response MUST be "[" or "{{"

Available tools: {tools}

Agent profiles:
- default: General purpose
- code: Code analysis and editing
- browser: Web browsing
- data: Data analysis
- im: IM channel operations (Feishu, Telegram, etc.)

Output format:
[
  {{
    "id": "step_1",
    "description": "Clear actionable description",
    "required_tools": ["tool_name1"],
    "agent_profile": "default",
    "depends_on": [],
    "estimated_complexity": "low"
  }}
]
"""

    def __init__(self, brain: Any | None = None):
        self._llm_client: Any | None = None
        self._brain = brain
        self._learning_store: Any | None = None
        self._last_learning_usage_summary: dict[str, Any] | None = None

    def _get_llm_client(self) -> Any:
        """Lazy import LLMClient to avoid circular deps."""
        if self._llm_client is None:
            from ..llm.client import LLMClient

            self._llm_client = LLMClient()
        return self._llm_client

    def _get_learning_store(self) -> Any | None:
        if self._learning_store is None:
            try:
                from ..learning.store import LearningStore

                self._learning_store = LearningStore()
            except Exception as exc:
                logger.debug("[TaskPlanner] LearningStore unavailable: %s", exc)
                self._learning_store = False
        return self._learning_store or None

    def get_last_learning_usage_summary(self) -> dict[str, Any] | None:
        if self._last_learning_usage_summary is None:
            return None
        return json.loads(json.dumps(self._last_learning_usage_summary, ensure_ascii=False))

    async def plan(
        self,
        task_description: str,
        context: str = "",
        max_sub_tasks: int = 5,
        available_tools: list[str] | None = None,
    ) -> list[SubTask]:
        """Alias for analyze() — decompose a task into sub-tasks."""
        return await self.analyze(
            task_description=task_description,
            context=context,
            max_sub_tasks=max_sub_tasks,
            available_tools=available_tools,
        )

    async def analyze(
        self,
        task_description: str,
        context: str = "",
        max_sub_tasks: int = 5,
        available_tools: list[str] | None = None,
    ) -> list[SubTask]:
        """Analyze a task and decompose into sub-tasks via LLM."""
        self._last_learning_usage_summary = None
        learning_actions = self._collect_learning_candidate_actions(task_description)
        rule_based = self._build_rule_based_plan(task_description, available_tools or [])
        if rule_based:
            logger.info(
                "[TaskPlanner] Using rule-based external action plan for task: %s",
                task_description[:120],
            )
            planned, usage_summary = self._apply_learning_candidate_actions(
                rule_based[:max_sub_tasks],
                learning_actions,
                max_sub_tasks=max_sub_tasks,
            )
            self._last_learning_usage_summary = usage_summary
            return planned

        tools_str = ", ".join(available_tools or ["all tools"])

        system = self.PLANNER_SYSTEM_PROMPT.format(
            max_sub_tasks=max_sub_tasks,
            tools=tools_str,
        )
        user_msg = f"Task: {task_description}\n\n"
        if context:
            user_msg += f"Context: {context}\n\n"
        learning_context = self._build_learning_context(learning_actions)
        learning_context_applied = bool(learning_context)
        if learning_context:
            user_msg += f"Historical remediation hints:\n{learning_context}\n\n"
        user_msg += f"Decompose into at most {max_sub_tasks} sub-tasks."

        try:
            if self._brain is not None:
                # Use Brain's think method if available
                response = await self._brain.think(
                    prompt=user_msg,
                    system=system,
                    max_tokens=4096,
                    enable_thinking=False,
                )
                content = response.content if hasattr(response, "content") else str(response)
            else:
                client = self._get_llm_client()
                response = await client.chat(
                    messages=[{"role": "user", "content": user_msg}],
                    system=system,
                    temperature=0.3,
                    max_tokens=4096,
                )
                content = response.content if hasattr(response, "content") else str(response)
            parsed = self._parse_sub_tasks(content)
            planned, usage_summary = self._apply_learning_candidate_actions(
                parsed,
                learning_actions,
                max_sub_tasks=max_sub_tasks,
            )
            usage_summary["learning_context_applied"] = learning_context_applied
            self._last_learning_usage_summary = usage_summary
            return planned

        except Exception as e:
            logger.error(f"[TaskPlanner] LLM analyze failed: {e}")
            # Fallback: single task
            fallback = canonicalize_sub_tasks(
                [
                    SubTask(
                        id="1",
                        description=task_description,
                        required_tools=[],
                        estimated_complexity="medium",
                    )
                ]
            )
            planned, usage_summary = self._apply_learning_candidate_actions(
                fallback,
                learning_actions,
                max_sub_tasks=max_sub_tasks,
            )
            usage_summary["learning_context_applied"] = learning_context_applied
            usage_summary["planning_mode"] = "fallback"
            self._last_learning_usage_summary = usage_summary
            return planned

    def _build_rule_based_plan(
        self,
        task_description: str,
        available_tools: list[str],
    ) -> list[SubTask]:
        """Use deterministic plans for common external-action workflows.

        These tasks should prefer executable browser / IM steps over codebase
        exploration. This avoids drifting into grep/read-file plans when the
        user actually wants a fresh QR code checked and re-delivered.
        """
        normalized = str(task_description or "").lower()
        if not normalized:
            return []

        has_qr = any(keyword in normalized for keyword in ("二维码", "qr"))
        has_delivery = any(keyword in normalized for keyword in ("飞书", "feishu", "推送", "发送"))
        has_login = any(keyword in normalized for keyword in ("登录", "登陆", "login"))
        has_refresh = any(keyword in normalized for keyword in ("重新", "最新", "检查", "完整", "刷新"))
        if not (has_qr and has_delivery and (has_login or has_refresh)):
            return []

        tool_set = set(available_tools or [])

        def _pick(*names: str) -> list[str]:
            return [name for name in names if name in tool_set]

        browser_open_tools = _pick("browser_open", "browser_navigate")
        capture_tools = _pick("browser_screenshot", "browser_execute_js", "view_image")
        deliver_tools = _pick("deliver_artifacts")

        if not browser_open_tools or not deliver_tools:
            return []

        inspect_tools = capture_tools or _pick("browser_snapshot", "browser_scroll")
        refresh_tools = _pick("browser_navigate", "browser_open", "browser_execute_js", "sleep")

        return canonicalize_sub_tasks([
            SubTask(
                id="1",
                description="访问登录页面并确认当前二维码展示区域",
                required_tools=browser_open_tools + [tool for tool in inspect_tools if tool not in browser_open_tools],
                agent_profile="browser",
                estimated_complexity="medium",
            ),
            SubTask(
                id="2",
                description="截取并检查二维码是否完整、是否为最新登录二维码",
                required_tools=inspect_tools or browser_open_tools,
                agent_profile="browser",
                depends_on=["1"],
                estimated_complexity="medium",
            ),
            SubTask(
                id="3",
                description="如二维码不完整或已失效，则重新刷新登录页面并重新获取完整二维码",
                required_tools=refresh_tools or browser_open_tools,
                agent_profile="browser",
                depends_on=["2"],
                estimated_complexity="medium",
            ),
            SubTask(
                id="4",
                description="将确认后的最新完整二维码推送到飞书",
                required_tools=deliver_tools,
                agent_profile="im",
                depends_on=["3"],
                estimated_complexity="low",
            ),
        ])

    @staticmethod
    def _tokenize_text(value: str) -> set[str]:
        raw = str(value or "").lower()
        if not raw:
            return set()
        raw_tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", raw)
        tokens: set[str] = set()
        for token in raw_tokens:
            token = token.strip()
            if not token:
                continue
            tokens.add(token)
            if re.fullmatch(r"[\u4e00-\u9fff]{4,}", token):
                for size in (2, 3, 4):
                    if len(token) < size:
                        continue
                    for index in range(0, len(token) - size + 1):
                        tokens.add(token[index : index + size])
        stop_words = {
            "请",
            "帮我",
            "一个",
            "一下",
            "继续",
            "处理",
            "任务",
            "步骤",
            "当前",
            "重新",
            "最终",
            "最新",
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
        }
        return {token for token in tokens if token not in stop_words and len(token.strip()) >= 2}

    def _collect_learning_candidate_actions(self, task_description: str) -> list[dict[str, Any]]:
        store = self._get_learning_store()
        if store is None:
            return []
        try:
            cases = store.list_reviewed_cases_with_candidate_actions(limit=40)
        except Exception as exc:
            logger.debug("[TaskPlanner] Failed to load reviewed candidate actions: %s", exc)
            return []

        task_tokens = self._tokenize_text(task_description)
        if not task_tokens:
            return []

        credit_scores = self._load_candidate_action_credit_scores(store)

        ranked: list[dict[str, Any]] = []
        for case in cases:
            case_tokens = self._tokenize_text(
                " ".join(
                    [
                        str(getattr(case, "problem_summary", "") or ""),
                        str(getattr(case, "outcome_summary", "") or ""),
                        str((getattr(case, "lineage", {}) or {}).get("task_message", "") or ""),
                        " ".join(str(tag) for tag in (getattr(case, "tags", []) or [])),
                    ]
                )
            )
            overlap = sorted(task_tokens & case_tokens)
            if len(overlap) < 2 and not any(token in str(task_description or "") for token in overlap):
                continue
            score = len(overlap)
            if "orchestration_result" in (getattr(case, "tags", []) or []):
                score += 1
            if getattr(case, "severity", "") == "high":
                score += 1
            actions: list[dict[str, Any]] = []
            for action in list(getattr(case, "candidate_actions", []) or []):
                if not isinstance(action, dict):
                    continue
                description = str(action.get("description", "") or "").strip()
                if not description:
                    continue
                action_type = str(action.get("action_type", "") or "").strip()
                target_id = self._build_candidate_action_target_id(
                    getattr(case, "case_id", ""),
                    action,
                )
                credit = credit_scores.get(target_id, {})
                helpful_count = int(credit.get("helpful_count", 0) or 0)
                neutral_count = int(credit.get("neutral_count", 0) or 0)
                harmful_count = int(credit.get("harmful_count", 0) or 0)
                effectiveness_score = (
                    helpful_count * 2
                    - harmful_count * 2
                    - neutral_count * 0.2
                    + float(credit.get("last_score", 0.0) or 0.0)
                )
                validation_status = self._derive_learning_action_validation_status(
                    action_type=action_type,
                    helpful_count=helpful_count,
                    harmful_count=harmful_count,
                )
                planner_reusable = self._is_planner_reusable_learning_action(
                    case=case,
                    action_type=action_type,
                    validation_status=validation_status,
                )
                if not planner_reusable:
                    continue
                actions.append(
                    {
                        **action,
                        "action_type": action_type,
                        "description": description,
                        "target_id": target_id,
                        "helpful_count": helpful_count,
                        "neutral_count": neutral_count,
                        "harmful_count": harmful_count,
                        "effectiveness_score": effectiveness_score,
                        "validation_status": validation_status,
                        "planner_reusable": planner_reusable,
                    }
                )
            if not actions:
                continue
            actions.sort(
                key=lambda item: (
                    -float(item.get("effectiveness_score", 0.0) or 0.0),
                    -int(item.get("helpful_count", 0) or 0),
                    int(item.get("harmful_count", 0) or 0),
                    str(item.get("description", "") or ""),
                )
            )
            ranked.append(
                {
                    "case_id": getattr(case, "case_id", ""),
                    "source_ref": getattr(case, "source_ref", ""),
                    "score": score + max(
                        (float(item.get("effectiveness_score", 0.0) or 0.0) for item in actions),
                        default=0.0,
                    ),
                    "overlap_tokens": overlap,
                    "problem_summary": str(getattr(case, "problem_summary", "") or ""),
                    "candidate_actions": actions,
                }
            )

        ranked.sort(key=lambda item: (-int(item.get("score", 0)), item.get("source_ref", "")))
        return ranked[:3]

    @staticmethod
    def _build_learning_context(learning_actions: list[dict[str, Any]]) -> str:
        if not learning_actions:
            return ""
        lines: list[str] = []
        for item in learning_actions[:3]:
            actions = [
                str(action.get("description", "") or "").strip()
                for action in (item.get("candidate_actions", []) or [])[:3]
                if isinstance(action, dict) and str(action.get("description", "") or "").strip()
            ]
            if not actions:
                continue
            problem = str(item.get("problem_summary", "") or "").strip()
            overlap = ",".join(item.get("overlap_tokens", [])[:5])
            lines.append(
                f"- 历史失败场景: {problem[:120]} | 命中词: {overlap or '无'} | 可复用补救动作: "
                + "；".join(actions)
            )
        return "\n".join(lines)

    @staticmethod
    def _build_candidate_action_target_id(case_id: str, action: dict[str, Any]) -> str:
        case_key = str(case_id or "").strip() or "unknown"
        step_id = str(action.get("step_id", "") or action.get("id", "") or "").strip() or "step"
        description = str(action.get("description", "") or "").strip().lower()
        normalized_desc = re.sub(r"\s+", " ", description)
        return f"{case_key}:{step_id}:{normalized_desc[:120]}"

    @staticmethod
    def _derive_learning_action_validation_status(
        *,
        action_type: str,
        helpful_count: int,
        harmful_count: int,
    ) -> str:
        normalized_type = str(action_type or "").strip()
        if normalized_type == "successful_candidate_action":
            return "validated_success"
        if normalized_type == "next_round_todo_step":
            if helpful_count > 0 and helpful_count >= harmful_count:
                return "validated_by_feedback"
            return "pending_feedback"
        return "legacy"

    @staticmethod
    def _is_planner_reusable_learning_action(
        *,
        case: Any,
        action_type: str,
        validation_status: str,
    ) -> bool:
        normalized_type = str(action_type or "").strip()
        if validation_status in {"validated_success", "validated_by_feedback"}:
            return True
        if normalized_type == "next_round_todo_step":
            return False
        tags = set(getattr(case, "tags", []) or [])
        if "successful_remediation" in tags or "candidate_action_reuse" in tags:
            return True
        return False

    @staticmethod
    def _load_candidate_action_credit_scores(store: Any) -> dict[str, dict[str, Any]]:
        if not hasattr(store, "list_credit_stats"):
            return {}
        try:
            stats = store.list_credit_stats(target_type="candidate_action", limit=500)
        except Exception as exc:
            logger.debug("[TaskPlanner] Failed to load candidate-action credit stats: %s", exc)
            return {}
        return {
            str(stat.target_id): {
                "helpful_count": int(getattr(stat, "helpful_count", 0) or 0),
                "neutral_count": int(getattr(stat, "neutral_count", 0) or 0),
                "harmful_count": int(getattr(stat, "harmful_count", 0) or 0),
                "last_score": float(getattr(stat, "last_score", 0.0) or 0.0),
            }
            for stat in stats
        }

    def _apply_learning_candidate_actions(
        self,
        tasks: list[SubTask],
        learning_actions: list[dict[str, Any]],
        *,
        max_sub_tasks: int,
    ) -> tuple[list[SubTask], dict[str, Any]]:
        normalized = canonicalize_sub_tasks(tasks or [])
        usage_summary = {
            "matched_case_count": len(learning_actions),
            "used_action_count": 0,
            "used_actions": [],
            "learning_context_applied": False,
            "planning_mode": "normal",
        }
        if not normalized or not learning_actions:
            return normalized, usage_summary

        existing_descriptions = {
            str(task.description or "").strip().lower()
            for task in normalized
            if str(task.description or "").strip()
        }
        appended: list[SubTask] = []
        used_actions: list[dict[str, Any]] = []
        for item in learning_actions:
            for action in (item.get("candidate_actions", []) or [])[:3]:
                if not isinstance(action, dict):
                    continue
                description = str(action.get("description", "") or "").strip()
                if not description:
                    continue
                normalized_desc = description.lower()
                if normalized_desc in existing_descriptions:
                    used_actions.append(
                        {
                            "target_id": str(action.get("target_id", "") or ""),
                            "case_id": str(item.get("case_id", "") or ""),
                            "source_ref": str(item.get("source_ref", "") or ""),
                            "step_id": str(action.get("step_id", "") or action.get("id", "") or ""),
                            "description": description,
                            "usage_mode": "matched_existing_plan",
                            "overlap_tokens": list(item.get("overlap_tokens", []) or [])[:6],
                            "effectiveness_score": float(
                                action.get("effectiveness_score", 0.0) or 0.0
                            ),
                        }
                    )
                    continue
                appended.append(
                    SubTask(
                        id=str(action.get("step_id", "")) or f"step_{len(normalized) + len(appended) + 1}",
                        description=description,
                        required_tools=[],
                        agent_profile="default",
                        depends_on=[],
                        estimated_complexity="medium",
                    )
                )
                used_actions.append(
                    {
                        "target_id": str(action.get("target_id", "") or ""),
                        "case_id": str(item.get("case_id", "") or ""),
                        "source_ref": str(item.get("source_ref", "") or ""),
                        "step_id": str(action.get("step_id", "") or action.get("id", "") or ""),
                        "description": description,
                        "usage_mode": "appended_to_plan",
                        "overlap_tokens": list(item.get("overlap_tokens", []) or [])[:6],
                        "effectiveness_score": float(action.get("effectiveness_score", 0.0) or 0.0),
                    }
                )
                existing_descriptions.add(normalized_desc)
                if len(normalized) + len(appended) >= max_sub_tasks:
                    break
            if len(normalized) + len(appended) >= max_sub_tasks:
                break

        if not appended:
            usage_summary["used_actions"] = used_actions
            usage_summary["used_action_count"] = len(used_actions)
            return normalized[:max_sub_tasks], usage_summary

        merged = normalized[: max(0, max_sub_tasks - len(appended))]
        last_existing_id = merged[-1].id if merged else None
        for action_task in appended:
            if last_existing_id:
                action_task.depends_on = [last_existing_id]
                last_existing_id = action_task.id
            merged.append(action_task)
        usage_summary["used_actions"] = used_actions
        usage_summary["used_action_count"] = len(used_actions)
        return canonicalize_sub_tasks(merged[:max_sub_tasks]), usage_summary

    def _parse_sub_tasks(self, content: str) -> list[SubTask]:
        """Parse LLM response into SubTask objects."""
        normalized = self._normalize_planner_output(content)
        data = self._extract_json_payload(normalized)
        if data is None:
            logger.error(f"[TaskPlanner] JSON parse failed, content={normalized[:200]}")
            return []

        if isinstance(data, list):
            return canonicalize_sub_tasks([SubTask.from_dict(item) for item in data])
        if isinstance(data, dict):
            # Try common wrapper keys
            for key in ("sub_tasks", "tasks", "plan", "result"):
                if key in data and isinstance(data[key], list):
                    return canonicalize_sub_tasks([SubTask.from_dict(item) for item in data[key]])
            # Single task wrapped in dict
            return canonicalize_sub_tasks([SubTask.from_dict(data)])

        return []

    @staticmethod
    def _normalize_planner_output(content: str) -> str:
        cleaned = str(content or "").strip()
        if not cleaned:
            return ""

        cleaned = re.sub(r"<thinking>.*?</thinking>\s*", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<think>.*?</think>\s*", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"</thinking>\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"</think>\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"<thinking>\s*.*$", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<think>\s*.*$", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        return cleaned.strip()

    @staticmethod
    def _extract_json_payload(content: str) -> Any | None:
        text = str(content or "").strip()
        if not text:
            return None

        direct = TaskPlanner._try_parse_json(text)
        if direct is not None:
            return direct

        for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE):
            candidate = match.group(1).strip()
            parsed = TaskPlanner._try_parse_json(candidate)
            if parsed is not None:
                return parsed

        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char not in "[{":
                continue
            try:
                parsed, _end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            return parsed

        return None

    @staticmethod
    def _try_parse_json(candidate: str) -> Any | None:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            return None


class TaskExecutor:
    """Executes a list of sub-tasks respecting dependencies."""

    def __init__(
        self,
        orchestrator: Any,
        *,
        on_step_status: Callable[[SubTask, str, dict[str, Any]], Awaitable[None] | None] | None = None,
        on_runtime_event: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ):
        self.orchestrator = orchestrator
        self.results: dict[str, str] = {}
        self._on_step_status = on_step_status
        self._on_runtime_event = on_runtime_event

    async def _notify_step_status(
        self,
        sub_task: SubTask,
        status: str,
        *,
        total_steps: int,
        completed_steps: int,
        failed_steps: int,
    ) -> None:
        if self._on_step_status is None:
            return
        try:
            maybe_result = self._on_step_status(
                sub_task,
                status,
                {
                    "total_steps": total_steps,
                    "completed_steps": completed_steps,
                    "failed_steps": failed_steps,
                },
            )
            if asyncio.iscoroutine(maybe_result):
                await maybe_result
        except Exception as exc:
            logger.warning("[TaskExecutor] Step status callback failed: %s", exc)

    async def _notify_runtime_event(self, event: dict[str, Any]) -> None:
        if self._on_runtime_event is None:
            return
        try:
            maybe_result = self._on_runtime_event(dict(event or {}))
            if asyncio.iscoroutine(maybe_result):
                await maybe_result
        except Exception as exc:
            logger.warning("[TaskExecutor] Runtime event callback failed: %s", exc)

    async def execute(
        self,
        sub_tasks: list[SubTask],
        session: Any,
        from_agent: str = "planner",
    ) -> dict[str, str]:
        """Execute sub-tasks in dependency order with parallelism."""
        completed: set[str] = set()
        self.results = {}

        while len(completed) < len(sub_tasks):
            # Find tasks whose dependencies are all satisfied
            ready = [
                t
                for t in sub_tasks
                if t.id not in completed
                and all(dep in completed for dep in t.depends_on)
            ]

            if not ready:
                remaining = [t.id for t in sub_tasks if t.id not in completed]
                logger.error(f"[TaskExecutor] Dependency deadlock: {remaining}")
                for tid in remaining:
                    self.results[tid] = "❌ Deadlock: dependencies cannot be satisfied"
                break

            # Execute ready tasks in parallel
            coros = [self._execute_one(t, session, from_agent) for t in ready]
            await asyncio.gather(*coros, return_exceptions=True)

            for t in ready:
                completed.add(t.id)
                completed_steps = sum(1 for step in sub_tasks if step.status == "completed")
                failed_steps = sum(1 for step in sub_tasks if step.status == "failed")
                await self._notify_step_status(
                    t,
                    t.status,
                    total_steps=len(sub_tasks),
                    completed_steps=completed_steps,
                    failed_steps=failed_steps,
                )

        return self.results

    async def _execute_one(
        self,
        sub_task: SubTask,
        session: Any,
        from_agent: str,
    ) -> None:
        """Execute a single sub-task via delegation."""
        sub_task.status = "running"
        await self._notify_step_status(
            sub_task,
            "in_progress",
            total_steps=0,
            completed_steps=0,
            failed_steps=0,
        )
        logger.info(
            f"[TaskExecutor] Running sub-task {sub_task.id}: {sub_task.description}"
        )
        await self._notify_runtime_event(
            {
                "type": "agent_handoff",
                "from_agent": from_agent,
                "to_agent": sub_task.agent_profile,
                "reason": f"执行子任务 {sub_task.id}: {sub_task.description}",
                "sub_task_id": sub_task.id,
                "sub_task_description": sub_task.description,
            }
        )
        await self._notify_runtime_event(
            {
                "type": "chain_text",
                "content": f"开始执行子任务 {sub_task.id}: {sub_task.description}",
                "sub_task_id": sub_task.id,
                "sub_task_description": sub_task.description,
            }
        )

        try:
            # Build delegation context with tool filter
            context_parts = [
                f"Sub-task {sub_task.id}: {sub_task.description}",
            ]
            if sub_task.required_tools:
                context_parts.append(
                    f"Required tools: {', '.join(sub_task.required_tools)}"
                )

            async def _progress_event_sink(event: dict[str, Any]) -> None:
                payload = dict(event or {})
                payload.setdefault("sub_task_id", sub_task.id)
                payload.setdefault("sub_task_description", sub_task.description)
                payload.setdefault("agent_profile", sub_task.agent_profile)
                await self._notify_runtime_event(payload)

            result = await self.orchestrator.delegate(
                session=session,
                from_agent=from_agent,
                to_agent=sub_task.agent_profile,
                message=sub_task.description,
                reason=f"Sub-task {sub_task.id} from plan",
                context="\n".join(context_parts),
                # Pass tool filter so sub-agent loads only needed tools
                tool_filter=sub_task.required_tools or None,
                progress_event_sink=_progress_event_sink,
            )

            sub_task.result = result
            sub_task.status = "completed"
            self.results[sub_task.id] = result
            logger.info(f"[TaskExecutor] Sub-task {sub_task.id} completed")
            await self._notify_runtime_event(
                {
                    "type": "chain_text",
                    "content": f"子任务 {sub_task.id} 已完成: {sub_task.description}",
                    "sub_task_id": sub_task.id,
                    "sub_task_description": sub_task.description,
                }
            )

        except Exception as e:
            logger.error(f"[TaskExecutor] Sub-task {sub_task.id} failed: {e}")
            sub_task.status = "failed"
            sub_task.result = str(e)
            self.results[sub_task.id] = f"❌ Failed: {e}"
            await self._notify_runtime_event(
                {
                    "type": "chain_text",
                    "content": f"子任务 {sub_task.id} 执行失败: {e}",
                    "sub_task_id": sub_task.id,
                    "sub_task_description": sub_task.description,
                }
            )

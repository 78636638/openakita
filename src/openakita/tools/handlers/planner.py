"""
Task Planner handler — decomposes complex tasks into sub-tasks.

Registered as 'planner' handler.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class PlannerToolHandler:
    """Handles plan_task tool calls."""

    TOOLS = ["plan_task"]

    def __init__(self, agent: Agent):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "plan_task":
            return await self._plan_task(params)
        return f"Unknown planner tool: {tool_name}"

    async def _plan_task(self, params: dict[str, Any]) -> str:
        """Decompose a task into sub-tasks using LLM."""
        task_description = params.get("task_description", "")
        context = params.get("context", "")
        max_sub_tasks = params.get("max_sub_tasks", 5)

        if not task_description:
            return json.dumps(
                {"error": "task_description is required"}, ensure_ascii=False
            )

        # Build available tools list
        available_tools = self._get_available_tools()

        # Build planner prompt
        system_prompt = self._build_system_prompt(max_sub_tasks, available_tools)
        user_prompt = self._build_user_prompt(task_description, context)

        try:
            from ...llm.client import LLMClient

            client = LLMClient()
            response = await client.chat(
                messages=[{"role": "user", "content": user_prompt}],
                system=system_prompt,
                temperature=0.3,
                max_tokens=4096,
            )

            content = response.content if hasattr(response, "content") else str(response)
            sub_tasks = self._parse_response(content)

            return json.dumps(sub_tasks, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"[Planner] Failed to plan task: {e}")
            # Fallback: return single task
            return json.dumps(
                [
                    {
                        "id": "1",
                        "description": task_description,
                        "required_tools": [],
                        "agent_profile": "default",
                        "depends_on": [],
                        "estimated_complexity": "medium",
                    }
                ],
                ensure_ascii=False,
                indent=2,
            )

    def _get_available_tools(self) -> list[str]:
        """Get list of available tool names from agent."""
        tools = getattr(self.agent, "_tools", [])
        return [t.get("name", "") for t in tools if t.get("name")]

    def _build_system_prompt(self, max_sub_tasks: int, available_tools: list[str]) -> str:
        """Build system prompt for task planning."""
        tools_str = ", ".join(available_tools[:50])  # Limit to avoid prompt bloat

        return f"""\
You are a Task Planner. Decompose user requests into executable sub-tasks.

Rules:
1. Each sub-task must be independent and verifiable
2. Minimize dependencies — parallelize whenever possible
3. required_tools MUST be chosen from the available tools list
4. Complexity: low (1 step), medium (2-3 steps), high (requires reasoning)
5. Maximum {max_sub_tasks} sub-tasks
6. Output strict JSON array only, no markdown

Available tools: {tools_str}

Output format:
[
  {{
    "id": "1",
    "description": "Clear actionable description",
    "required_tools": ["tool_name1", "tool_name2"],
    "agent_profile": "default",
    "depends_on": [],
    "estimated_complexity": "low"
  }}
]

Agent profiles:
- default: General purpose agent
- code: Code analysis and editing
- browser: Web browsing and scraping
- data: Data analysis and processing
- im: IM channel operations (Feishu, Telegram, etc.)
"""

    def _build_user_prompt(self, task_description: str, context: str) -> str:
        """Build user prompt for task planning."""
        prompt = f"Task: {task_description}\n\n"
        if context:
            prompt += f"Context: {context}\n\n"
        prompt += "Decompose this task into sub-tasks."
        return prompt

    def _parse_response(self, content: str) -> list[dict]:
        """Parse LLM response into sub-task list."""
        # Try to extract JSON from markdown code blocks
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            # Remove first and last code fence lines
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines)

        try:
            data = json.loads(content)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "sub_tasks" in data:
                return data["sub_tasks"]
            return [data]
        except json.JSONDecodeError:
            logger.error(f"[Planner] Failed to parse response: {content[:200]}")
            return []


def create_handler(agent: "Agent"):
    """Factory function for PlannerToolHandler."""
    handler = PlannerToolHandler(agent)
    return handler.handle

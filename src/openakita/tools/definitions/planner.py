"""
Task Planner tool definition.

Provides plan_task for decomposing complex user requests into sub-tasks.
Each sub-task can be delegated to a specialized agent with a targeted tool set.
"""

from .base import ToolDefinition

PLANNER_TOOLS: list[ToolDefinition] = [
    {
        "name": "plan_task",
        "category": "System",
        "description": (
            "Decompose a complex task into independent sub-tasks that can be executed "
            "in parallel or sequence. Each sub-task specifies the required tools, "
            "agent profile, and dependencies. Invoke when a user request involves "
            "multiple steps, crosses domains, or would benefit from parallel execution."
        ),
        "detail": (
            "## When to Use\n"
            "- The user request involves multiple distinct steps\n"
            "- Different sub-tasks need different tool categories (e.g., file + web + IM)\n"
            "- Sub-tasks can be executed in parallel to save time\n"
            "- The task is exploratory and may need branching\n\n"
            "## Output Format\n"
            "Returns a JSON array of sub-tasks. Each sub-task has:\n"
            "- **id**: Unique identifier (1, 2, 3...)\n"
            "- **description**: Clear, actionable description\n"
            "- **required_tools**: List of tool names needed\n"
            "- **agent_profile**: Recommended agent type (default, code, browser, data, im)\n"
            "- **depends_on**: IDs of sub-tasks that must complete first\n"
            "- **estimated_complexity**: low / medium / high\n\n"
            "## Example\n"
            'User: "排查飞书推送问题并修复"\n'
            "→ Sub-task 1: 检查配置 (tools: read_file, grep)\n"
            "→ Sub-task 2: 测试推送 (tools: deliver_artifacts, depends_on: [1])"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_description": {
                    "type": "string",
                    "description": "The user request to decompose",
                },
                "context": {
                    "type": "string",
                    "description": "Additional context (known info, constraints)",
                },
                "max_sub_tasks": {
                    "type": "integer",
                    "description": "Maximum number of sub-tasks (default: 5)",
                    "default": 5,
                },
            },
            "required": ["task_description"],
        },
        "examples": [
            {
                "scenario": "排查飞书推送问题",
                "params": {
                    "task_description": "飞书收不到二维码推送消息，帮我排查",
                    "context": "系统之前可以正常推送，最近修改了 Token 配置",
                    "max_sub_tasks": 3,
                },
                "expected": (
                    '[{"id":"1","description":"检查飞书配置和Token",'
                    '"required_tools":["read_file","grep"],"agent_profile":"default",'
                    '"depends_on":[],"estimated_complexity":"low"},'
                    '{"id":"2","description":"查看飞书通道状态",'
                    '"required_tools":["get_chat_history"],"agent_profile":"im",'
                    '"depends_on":[],"estimated_complexity":"low"},'
                    '{"id":"3","description":"测试推送消息到飞书",'
                    '"required_tools":["deliver_artifacts"],"agent_profile":"im",'
                    '"depends_on":["1","2"],"estimated_complexity":"medium"}]'
                ),
            }
        ],
    }
]

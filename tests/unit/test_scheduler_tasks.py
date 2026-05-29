"""L1 Unit Tests: Scheduled task creation, state transitions, and triggers."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from openakita.scheduler.executor import TaskExecutor
from openakita.scheduler.task import ScheduledTask, TaskStatus, TaskType, TriggerType
from openakita.scheduler.triggers import CronTrigger, IntervalTrigger, OnceTrigger, Trigger


class TestScheduledTaskCreation:
    def test_create_basic_task(self):
        task = ScheduledTask.create(
            name="test-task",
            description="A test task",
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": (datetime.now() + timedelta(hours=1)).isoformat()},
            prompt="Do something",
        )
        assert task.name == "test-task"
        assert task.status == TaskStatus.PENDING
        assert task.enabled is True

    def test_create_reminder(self):
        run_at = datetime.now() + timedelta(hours=2)
        task = ScheduledTask.create_reminder(
            name="birthday-reminder",
            description="Remind about birthday",
            run_at=run_at,
            message="Happy birthday!",
        )
        assert task.is_reminder is True
        assert task.reminder_message == "Happy birthday!"

    def test_create_interval_task(self):
        task = ScheduledTask.create_interval(
            name="hourly-check",
            description="Check every hour",
            interval_minutes=60,
            prompt="Run health check",
        )
        assert task.trigger_type == TriggerType.INTERVAL

    def test_create_cron_task(self):
        task = ScheduledTask.create_cron(
            name="daily-report",
            description="Generate daily report",
            cron_expression="0 8 * * *",
            prompt="Generate report",
        )
        assert task.trigger_type == TriggerType.CRON


class TestTaskStateTransitions:
    def test_enable_disable(self):
        task = ScheduledTask.create(
            name="t", description="d",
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": datetime.now().isoformat()},
            prompt="p",
        )
        task.disable()
        assert task.enabled is False
        task.enable()
        assert task.enabled is True

    def test_mark_running(self):
        task = ScheduledTask.create(
            name="t", description="d",
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": datetime.now().isoformat()},
            prompt="p",
        )
        task.mark_running()
        assert task.status == TaskStatus.RUNNING

    def test_mark_completed(self):
        task = ScheduledTask.create(
            name="t", description="d",
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": datetime.now().isoformat()},
            prompt="p",
        )
        task.mark_running()
        task.mark_completed()
        assert task.status == TaskStatus.COMPLETED
        assert task.run_count == 1

    def test_mark_failed(self):
        task = ScheduledTask.create(
            name="t", description="d",
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": datetime.now().isoformat()},
            prompt="p",
        )
        task.mark_running()
        task.mark_failed("timeout")
        # After failure, task may go to FAILED or back to SCHEDULED for retry
        assert task.status in (TaskStatus.FAILED, TaskStatus.SCHEDULED)
        assert task.fail_count == 1


class TestSystemTaskTimeouts:
    async def test_daily_memory_timeout_is_safe_pause_not_failure(self, monkeypatch):
        async def fake_wait_for(_coro, timeout):
            if hasattr(_coro, "close"):
                _coro.close()
            raise TimeoutError

        monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
        task = ScheduledTask.create(
            name="daily memory",
            description="daily memory",
            trigger_type=TriggerType.INTERVAL,
            trigger_config={"interval_minutes": 60},
            prompt="",
            action="system:daily_memory",
            deletable=False,
        )
        executor = TaskExecutor()

        success, message = await executor._execute_system_task(task)

        assert success is True
        assert "下次" in message

    async def test_daily_selfcheck_timeout_is_safe_pause_not_failure(self, monkeypatch):
        async def fake_wait_for(_coro, timeout):
            if hasattr(_coro, "close"):
                _coro.close()
            raise TimeoutError

        monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
        task = ScheduledTask.create(
            name="daily selfcheck",
            description="daily selfcheck",
            trigger_type=TriggerType.CRON,
            trigger_config={"cron": "0 4 * * *"},
            prompt="",
            action="system:daily_selfcheck",
            deletable=False,
        )
        executor = TaskExecutor()

        success, message = await executor._execute_system_task(task)

        assert success is True
        assert "下次继续" in message

    async def test_system_task_sets_and_resets_background_token_budget(self, monkeypatch):
        from openakita.core.token_tracking import get_token_budget, record_usage

        task = ScheduledTask.create(
            name="daily memory",
            description="daily memory",
            trigger_type=TriggerType.INTERVAL,
            trigger_config={"interval_minutes": 60},
            prompt="",
            action="system:daily_memory",
            deletable=False,
        )
        executor = TaskExecutor()

        async def fake_daily_memory():
            assert get_token_budget() is not None
            record_usage(input_tokens=10, output_tokens=5)
            return True, "done"

        monkeypatch.setattr(
            "openakita.config.settings.scheduler_background_token_budget",
            20,
        )
        monkeypatch.setattr(executor, "_system_daily_memory", fake_daily_memory)

        success, message = await executor._execute_system_task(task)

        assert success is True
        assert message == "done"
        assert get_token_budget() is None

    async def test_learning_ingest_system_task_runs_hook(self, monkeypatch):
        task = ScheduledTask.create(
            name="learning ingest",
            description="learning ingest",
            trigger_type=TriggerType.INTERVAL,
            trigger_config={"interval_minutes": 60},
            prompt="",
            action="system:hourly_learning_ingest",
            deletable=False,
        )
        executor = TaskExecutor()

        monkeypatch.setattr(
            "openakita.config.settings.learning_loop_enabled",
            True,
        )
        monkeypatch.setattr(
            "openakita.config.settings.learning_ingest_enabled",
            True,
        )
        monkeypatch.setattr(
            "openakita.learning.scheduler_hooks.run_learning_ingest",
            lambda: (3, 2),
        )

        success, message = await executor._execute_system_task(task)

        assert success is True
        assert "扫描 3 个分析文件" in message
        assert "新增 2 个 LearningCase" in message

    async def test_learning_review_system_task_runs_hook_with_memory_manager(self, monkeypatch):
        task = ScheduledTask.create(
            name="learning review",
            description="learning review",
            trigger_type=TriggerType.CRON,
            trigger_config={"cron": "0 5 * * *"},
            prompt="",
            action="system:daily_learning_review",
            deletable=False,
        )
        executor = TaskExecutor()
        executor.memory_manager = object()
        captured: dict[str, object] = {}

        def fake_run_learning_review(memory_manager):
            captured["memory_manager"] = memory_manager
            return 4, 1

        monkeypatch.setattr(
            "openakita.config.settings.learning_loop_enabled",
            True,
        )
        monkeypatch.setattr(
            "openakita.config.settings.learning_ingest_enabled",
            True,
        )
        monkeypatch.setattr(
            "openakita.learning.scheduler_hooks.run_learning_review",
            fake_run_learning_review,
        )

        success, message = await executor._execute_system_task(task)

        assert success is True
        assert captured["memory_manager"] is executor.memory_manager
        assert "回顾 4 个 LearningCase" in message
        assert "写入 1 条长期记忆" in message

    async def test_learning_shadow_system_task_runs_hook(self, monkeypatch):
        task = ScheduledTask.create(
            name="learning shadow",
            description="learning shadow",
            trigger_type=TriggerType.CRON,
            trigger_config={"cron": "30 5 * * *"},
            prompt="",
            action="system:learning_shadow",
            deletable=False,
        )
        executor = TaskExecutor()

        monkeypatch.setattr(
            "openakita.config.settings.learning_loop_enabled",
            True,
        )
        monkeypatch.setattr(
            "openakita.config.settings.learning_scheduler_primary",
            True,
        )
        monkeypatch.setattr(
            "openakita.learning.shadow.run_learning_shadow",
            lambda: {
                "planned_cases": 2,
                "generated_actions": 3,
                "shadowed_actions": 2,
                "skipped_existing_records": 1,
            },
        )

        success, message = await executor._execute_system_task(task)

        assert success is True
        assert "规划 2 个案例" in message
        assert "生成 3 个候选动作" in message
        assert "dry-run 2 个动作" in message
        assert "跳过 1 个已有记录" in message

    async def test_learning_verifier_system_task_runs_hook(self, monkeypatch):
        task = ScheduledTask.create(
            name="learning verifier",
            description="learning verifier",
            trigger_type=TriggerType.CRON,
            trigger_config={"cron": "0 6 * * *"},
            prompt="",
            action="system:learning_verifier",
            deletable=False,
        )
        executor = TaskExecutor()

        monkeypatch.setattr(
            "openakita.config.settings.learning_loop_enabled",
            True,
        )
        monkeypatch.setattr(
            "openakita.config.settings.learning_scheduler_primary",
            True,
        )
        monkeypatch.setattr(
            "openakita.learning.verifier.run_learning_verifier",
            lambda: {
                "attempted_actions": 3,
                "verified_actions": 2,
                "rolled_back_actions": 1,
            },
        )

        success, message = await executor._execute_system_task(task)

        assert success is True
        assert "尝试验证 3 个动作" in message
        assert "通过 2 个" in message
        assert "回滚 1 个" in message

    async def test_learning_promote_system_task_runs_hook(self, monkeypatch):
        task = ScheduledTask.create(
            name="learning promote",
            description="learning promote",
            trigger_type=TriggerType.CRON,
            trigger_config={"cron": "45 5 * * *"},
            prompt="",
            action="system:learning_promote",
            deletable=False,
        )
        executor = TaskExecutor()

        monkeypatch.setattr(
            "openakita.config.settings.learning_loop_enabled",
            True,
        )
        monkeypatch.setattr(
            "openakita.config.settings.learning_scheduler_primary",
            True,
        )
        monkeypatch.setattr(
            "openakita.learning.promote.run_learning_promote",
            lambda: {
                "eligible_actions": 3,
                "promoted_actions": 2,
                "rejected_actions": 1,
            },
        )

        success, message = await executor._execute_system_task(task)

        assert success is True
        assert "发现 3 个可提升动作" in message
        assert "成功 apply 2 个" in message
        assert "拒绝 1 个" in message

    async def test_daily_evaluation_system_task_runs_hook(self, monkeypatch):
        task = ScheduledTask.create(
            name="daily evaluation",
            description="daily evaluation",
            trigger_type=TriggerType.CRON,
            trigger_config={"cron": "30 4 * * *"},
            prompt="",
            action="system:daily_evaluation",
            deletable=False,
        )
        executor = TaskExecutor()

        async def fake_run_daily_evaluation(*, brain=None):
            return {
                "status": "completed",
                "traces_evaluated": 5,
                "generated_cases": 3,
                "inserted_cases": 2,
            }

        monkeypatch.setattr("openakita.config.settings.evaluation_enabled", True)
        monkeypatch.setattr(
            "openakita.learning.scheduler_hooks.run_daily_evaluation",
            fake_run_daily_evaluation,
        )

        success, message = await executor._execute_system_task(task)

        assert success is True
        assert "评估 5 个 trace" in message
        assert "生成 3 个 LearningCase" in message
        assert "新增 2 个案例" in message


class TestTaskAgentProfiles:
    async def test_executor_creates_selected_agent_profile(self, monkeypatch):
        selected = SimpleNamespace(id="code-assistant")
        created = SimpleNamespace(name="profile-agent")
        seen: dict[str, object] = {}

        async def fake_create(self, profile):
            seen["profile"] = profile
            return created

        monkeypatch.setattr(
            TaskExecutor,
            "_resolve_agent_profile",
            lambda self, profile_id: selected if profile_id == "code-assistant" else None,
        )
        monkeypatch.setattr("openakita.agents.factory.AgentFactory.create", fake_create)

        executor = TaskExecutor()
        agent = await executor._create_agent("code-assistant")

        assert agent is created
        assert seen["profile"] is selected

    async def test_chat_created_task_inherits_current_agent_profile(self):
        from openakita.tools.handlers.scheduled import ScheduledHandler

        captured: dict[str, ScheduledTask] = {}

        class FakeScheduler:
            async def add_task(self, task):
                captured["task"] = task
                return task.id

        agent = SimpleNamespace(
            task_scheduler=FakeScheduler(),
            _current_session=SimpleNamespace(
                context=SimpleNamespace(agent_profile_id="researcher")
            ),
            _agent_profile_id="default",
        )
        handler = ScheduledHandler(agent)

        result = await handler._schedule_task(
            {
                "name": "research-task",
                "description": "research",
                "task_type": "task",
                "trigger_type": "interval",
                "trigger_config": {"interval_minutes": 60},
                "prompt": "do research",
            }
        )

        assert "已创建" in result
        assert captured["task"].agent_profile_id == "researcher"


class TestSystemTaskRegistration:
    async def test_memory_task_keeps_user_custom_trigger(self, monkeypatch):
        from openakita.core.agent import Agent

        task = ScheduledTask(
            id="system_daily_memory",
            name="记忆整理",
            trigger_type=TriggerType.INTERVAL,
            trigger_config={"interval_minutes": 720},
            action="system:daily_memory",
            prompt="",
            description="custom",
            task_type=TaskType.TASK,
            deletable=False,
            metadata={"user_custom_trigger": True},
        )

        class FakeTracker:
            def __init__(self, *_args, **_kwargs):
                pass

            def is_onboarding(self, _days):
                return False

        class FakeScheduler:
            def __init__(self):
                self.updates: list[tuple[str, dict]] = []
                self.saved = False

            def list_tasks(self):
                return [task]

            def get_task(self, task_id):
                return task if task_id == "system_daily_memory" else None

            async def update_task(self, task_id, updates):
                self.updates.append((task_id, updates))
                return True

            async def save(self):
                self.saved = True

            async def add_task(self, _task):
                return _task.id

        monkeypatch.setattr(
            "openakita.scheduler.consolidation_tracker.ConsolidationTracker",
            FakeTracker,
        )
        scheduler = FakeScheduler()
        agent = SimpleNamespace(task_scheduler=scheduler)

        await Agent._register_system_tasks(agent)

        assert scheduler.updates == []
        assert task.trigger_type == TriggerType.INTERVAL
        assert task.trigger_config == {"interval_minutes": 720}

    async def test_register_system_tasks_adds_learning_tasks(self, monkeypatch):
        from openakita.config import settings
        from openakita.core.agent import Agent

        class FakeTracker:
            def __init__(self, *_args, **_kwargs):
                pass

            def is_onboarding(self, _days):
                return False

        class FakeScheduler:
            def __init__(self):
                self.added: list[ScheduledTask] = []

            def list_tasks(self):
                return []

            def get_task(self, _task_id):
                return None

            async def update_task(self, _task_id, _updates):
                return True

            async def save(self):
                return None

            async def add_task(self, task):
                self.added.append(task)
                return task.id

            async def disable_task(self, _task_id):
                return True

            async def enable_task(self, _task_id):
                return True

        monkeypatch.setattr(
            "openakita.scheduler.consolidation_tracker.ConsolidationTracker",
            FakeTracker,
        )
        monkeypatch.setattr(
            "openakita.workspace.backup.read_backup_settings",
            lambda _project_root: {"enabled": False, "backup_path": ""},
        )
        monkeypatch.setattr(settings, "proactive_enabled", False)
        monkeypatch.setattr(settings, "memory_nudge_enabled", False)
        monkeypatch.setattr(settings, "learning_loop_enabled", True)
        monkeypatch.setattr(settings, "learning_ingest_enabled", True)
        monkeypatch.setattr(settings, "learning_scheduler_primary", True)

        scheduler = FakeScheduler()
        agent = SimpleNamespace(task_scheduler=scheduler)

        await Agent._register_system_tasks(agent)

        added_ids = {task.id for task in scheduler.added}
        ingest_task = next(task for task in scheduler.added if task.id == "system_learning_ingest")
        review_task = next(task for task in scheduler.added if task.id == "system_learning_review")
        shadow_task = next(task for task in scheduler.added if task.id == "system_learning_shadow")
        promote_task = next(task for task in scheduler.added if task.id == "system_learning_promote")
        verifier_task = next(task for task in scheduler.added if task.id == "system_learning_verifier")

        assert "system_learning_ingest" in added_ids
        assert "system_learning_review" in added_ids
        assert "system_learning_shadow" in added_ids
        assert "system_learning_promote" in added_ids
        assert "system_learning_verifier" in added_ids
        assert ingest_task.action == "system:hourly_learning_ingest"
        assert ingest_task.trigger_type == TriggerType.INTERVAL
        assert ingest_task.trigger_config == {"interval_minutes": 60}
        assert ingest_task.deletable is False
        assert review_task.action == "system:daily_learning_review"
        assert review_task.trigger_type == TriggerType.CRON
        assert review_task.trigger_config == {"cron": "0 5 * * *"}
        assert review_task.deletable is False
        assert shadow_task.action == "system:learning_shadow"
        assert shadow_task.trigger_type == TriggerType.CRON
        assert shadow_task.trigger_config == {"cron": "30 5 * * *"}
        assert shadow_task.deletable is False
        assert promote_task.action == "system:learning_promote"
        assert promote_task.trigger_type == TriggerType.CRON
        assert promote_task.trigger_config == {"cron": "45 5 * * *"}
        assert promote_task.deletable is False
        assert verifier_task.action == "system:learning_verifier"
        assert verifier_task.trigger_type == TriggerType.CRON
        assert verifier_task.trigger_config == {"cron": "0 6 * * *"}
        assert verifier_task.deletable is False

    async def test_register_system_tasks_adds_daily_evaluation_task(self, monkeypatch):
        from openakita.config import settings
        from openakita.core.agent import Agent

        class FakeTracker:
            def __init__(self, *_args, **_kwargs):
                pass

            def is_onboarding(self, _days):
                return False

        class FakeScheduler:
            def __init__(self):
                self.added: list[ScheduledTask] = []

            def list_tasks(self):
                return []

            def get_task(self, _task_id):
                return None

            async def update_task(self, _task_id, _updates):
                return True

            async def save(self):
                return None

            async def add_task(self, task):
                self.added.append(task)
                return task.id

            async def disable_task(self, _task_id):
                return True

            async def enable_task(self, _task_id):
                return True

        monkeypatch.setattr(
            "openakita.scheduler.consolidation_tracker.ConsolidationTracker",
            FakeTracker,
        )
        monkeypatch.setattr(
            "openakita.workspace.backup.read_backup_settings",
            lambda _project_root: {"enabled": False, "backup_path": ""},
        )
        monkeypatch.setattr(settings, "proactive_enabled", False)
        monkeypatch.setattr(settings, "memory_nudge_enabled", False)
        monkeypatch.setattr(settings, "learning_loop_enabled", False)
        monkeypatch.setattr(settings, "evaluation_enabled", True)

        scheduler = FakeScheduler()
        agent = SimpleNamespace(task_scheduler=scheduler)

        await Agent._register_system_tasks(agent)

        evaluation_task = next(task for task in scheduler.added if task.id == "system_daily_evaluation")
        assert evaluation_task.action == "system:daily_evaluation"
        assert evaluation_task.trigger_type == TriggerType.CRON
        assert evaluation_task.trigger_config == {"cron": "30 4 * * *"}
        assert evaluation_task.deletable is False


class TestTaskSerialization:
    def test_to_dict_and_back(self):
        task = ScheduledTask.create(
            name="serialize-test", description="Test serialization",
            trigger_type=TriggerType.INTERVAL,
            trigger_config={"interval_minutes": 30},
            prompt="Do it",
        )
        d = task.to_dict()
        assert d["name"] == "serialize-test"
        restored = ScheduledTask.from_dict(d)
        assert restored.name == task.name
        assert restored.prompt == task.prompt


class TestTriggers:
    def test_once_trigger_fires_once(self):
        run_at = datetime.now() + timedelta(seconds=-1)
        trigger = OnceTrigger(run_at=run_at)
        assert trigger.should_run() is True
        trigger.mark_fired()
        assert trigger.should_run() is False

    def test_interval_trigger_next_run(self):
        trigger = IntervalTrigger(interval_minutes=60)
        next_run = trigger.get_next_run_time(last_run=datetime.now())
        assert next_run > datetime.now()
        assert (next_run - datetime.now()).total_seconds() < 3700  # ~60 min

    def test_cron_trigger_next_run(self):
        trigger = CronTrigger(cron_expression="0 8 * * *")
        next_run = trigger.get_next_run_time()
        assert next_run is not None
        assert next_run > datetime.now()

    def test_cron_trigger_describe(self):
        trigger = CronTrigger(cron_expression="0 8 * * *")
        desc = trigger.describe()
        assert isinstance(desc, str)
        assert len(desc) > 0

    def test_trigger_from_config(self):
        trigger = Trigger.from_config("once", {"run_at": (datetime.now() + timedelta(hours=1)).isoformat()})
        assert isinstance(trigger, OnceTrigger)

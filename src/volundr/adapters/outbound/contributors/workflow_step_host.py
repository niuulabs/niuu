"""Mark a Forge session as a workflow step host.

A workflow run launches one kept-alive harness session per stage member. The launch request's
``workflow_step_host`` flag becomes ``values.workflowSteps.enabled`` in the session spec, and
every pod manager hands it to Skuld as ``workflow_steps.enabled`` (the config file the chart
renders, or ``SKULD__WORKFLOW_STEPS__ENABLED`` for the process, container, VM and sandbox
runtimes). Skuld serves the step API and offers the ``workflow_step_close`` tool only then,
so an ordinary session pays for none of it.
"""

from volundr.domain.models import Session
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor
from volundr.domain.workflow_session import WORKFLOW_STEP_HOST_KEY, WORKFLOW_STEPS_VALUES_KEY


class WorkflowStepHostContributor(SessionContributor):
    """Turn the launch's step-host flag into Skuld's workflow step setting."""

    @property
    def name(self) -> str:
        return "workflow_step_host"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        if context.workload_config.get(WORKFLOW_STEP_HOST_KEY) is not True:
            return SessionContribution()
        return SessionContribution(values={WORKFLOW_STEPS_VALUES_KEY: {"enabled": True}})

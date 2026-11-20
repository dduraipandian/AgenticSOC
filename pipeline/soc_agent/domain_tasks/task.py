from pipeline.soc_agent.agent import Agent
from pipeline.soc_agent.tasks import TaskOption


class DomainTask:
    def __init__(self, task_option: TaskOption, agent: Agent):
        self.task_option = task_option
        self.agent = agent

    def run(self, question, **options):
        return self.agent.execute(question, self.task_option)



from pipeline.soc_agent.tools.tools import InputSchema, ToolFunction

parameters=InputSchema(
    type = "object",
    properties = {
        "evidence": {"type": "object"},
        "verdict": {"type": "string"}
    },
    required = ["evidence", "verdict"]
),


@ToolFunction()(parameters=parameters)
def pretty_json(evidence: dict, verdict: str) -> dict:
    """pretty prints input into python json dict"""
    return {
        "evidence": evidence,
        "verdict": verdict
    }


# class PrettyJson(ToolFunction):
#     @classmethod
#     def get_tool_definition(cls) -> Tool:
#         func = pretty_json
#         name=func.__name__

#         if not func.__doc__:
#             raise ToolDescriptionNotFound(f"function description not found for {name} in tool {cls.__name__}")

#         return Tool(name=name,
#                     description=func.__doc__,
#                     parameters=InputSchema(
#                         type = "object",
#                         properties = {
#                             "evidence": {"type": "object"},
#                             "verdict": {"type": "string"}
#                         },
#                         required = ["evidence", "verdict"]
#                     ),
#                     function=func
#                 )

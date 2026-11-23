"""Progress events emitted from inside graph nodes with LangGraph's stream writer.

They reach a caller that streams with stream_mode="custom" (the API turns them into Server Sent
Events). Under plain invoke() the writer is a no-op, and outside a graph run (unit tests calling
a node directly) emit() does nothing.

Event types: review_started, route_decided, agent_started, agent_finished, agent_failed,
validate_done, judge_round, awaiting_approval, posting, posted. (The API adds error.)
"""

from langgraph.config import get_stream_writer


def emit(event_type: str, **data) -> None:
    try:
        writer = get_stream_writer()
    except RuntimeError:  # not inside a graph run
        return
    writer({"type": event_type, **data})

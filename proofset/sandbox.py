"""The run_python tool: the solver agents write a program, AWS Lambda runs it."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

FUNCTION = os.environ.get("PROOFSET_SANDBOX_FUNCTION", "proofset-sandbox")
REGION = os.environ.get("AWS_REGION", "us-east-1")
Invoke = Callable[[dict[str, Any]], dict[str, Any]]


def lambda_invoke(payload: dict[str, Any]) -> dict[str, Any]:
    import boto3

    response = boto3.client("lambda", region_name=REGION).invoke(
        FunctionName=FUNCTION, Payload=json.dumps(payload).encode())
    body = json.loads(response["Payload"].read())
    if response.get("FunctionError"):
        return {"stdout": "", "stderr": body.get("errorMessage", "sandbox error"), "exit_code": None,
                "timed_out": False}
    return body


def format_result(result: dict[str, Any]) -> str:
    status = "timed out" if result.get("timed_out") else f"exit code {result.get('exit_code')}"
    parts = [status, "stdout:", result.get("stdout") or "(nothing printed)"]
    if result.get("stderr"):
        parts += ["stderr:", result["stderr"]]
    return "\n".join(parts)


def fix_escaped_newlines(code: str) -> str:
    """Some models send a whole program on one line with literal "\\n" for line breaks, then resend it
    after every SyntaxError. Swap them for real line breaks, only if that makes the code compile."""
    if "\n" in code.strip() or "\\n" not in code:
        return code
    try:
        compile(code, "<run_python>", "exec")
        return code
    except SyntaxError:
        fixed = code.replace("\\n", "\n").replace("\\t", "\t")
    try:
        compile(fixed, "<run_python>", "exec")
        return fixed
    except SyntaxError:
        return code


def make_run_python(invoke: Invoke = lambda_invoke, log: list | None = None):
    """A Strands tool. `log`, if given, collects {"code", "result"} for every run."""
    from strands import tool

    @tool
    def run_python(code: str) -> str:
        """Run a short Python 3 program in an isolated sandbox and return what it printed.

        Use it for every calculation. Standard library only, 5-second limit.

        Args:
            code: A complete Python 3 program. Print the final result on the last line.
        """
        code = fix_escaped_newlines(code)
        result = invoke({"code": code})
        if log is not None:
            log.append({"code": code, "result": result})
        return format_result(result)

    return run_python

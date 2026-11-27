"""Workaround for genlayer-test 0.30.2 direct mode with GenVM v0.4 runners.

gltest's LLM mock json-parses the mocked response and hands a dict to the SDK,
but the v0.4 SDK's exec_prompt(response_format="json") expects the raw JSON
*text* and parses it itself ("invalid nondeterministic response: JSON result
is not text"). Re-serialise so the SDK sees what a real node returns.
"""
import json

import gltest.direct.wasi_mock as wasi_mock

_original = wasi_mock._handle_llm_request


def _llm_as_text(vm, data):
    res = _original(vm, data)
    if isinstance(res, dict) and isinstance(res.get("ok"), (dict, list)):
        return {"ok": json.dumps(res["ok"])}
    return res


wasi_mock._handle_llm_request = _llm_as_text

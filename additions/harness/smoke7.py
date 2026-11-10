"""E7 smoke (inside the real tenant network): one call per parameter variant; prints status, finish reason, tokens,
the answer, and which request fields the provider received (field names only)."""
import json, os, httpx
key = json.loads(os.environ["ITPRO_STATE_REAL"])["keys"]["real-app"]
note = "Rewrite: Patient Maria Alvarez (MRN: 7551182) called (617) 555-0142 on 03/14/2024."
cases = [("sonnet A: max_tokens only", "real-sonnet", "recorder-anthropic", {"max_tokens": 200}),
         ("sonnet B: thinking between_tools", "real-sonnet", "recorder-anthropic",
          {"max_tokens": 200, "thinking": {"type": "between_tools"}}),
         ("mini: as E4-real", "real-mini", "recorder-openai", {"max_completion_tokens": 100, "reasoning_effort": "minimal"})]
with httpx.Client(timeout=180) as c:
    for name, model, rec, extra in cases:
        since = max([e["seq"] for e in c.get(f"http://{rec}:8080/_log").json()] or [0])
        r = c.post("http://gateway:4000/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                   json={"model": model, "messages": [{"role": "user", "content": note}], **extra})
        log = c.get(f"http://{rec}:8080/_log", params={"since": since}).json()
        sent = log[-1]["request"] if log else {}
        out = {"case": name, "status": r.status_code}
        if r.status_code == 200:
            j = r.json(); out.update({"finish": j["choices"][0].get("finish_reason"), "usage": {k: j["usage"].get(k) for k in ("prompt_tokens", "completion_tokens")},
                                      "answer": (j["choices"][0]["message"].get("content") or "")[:160]})
        else:
            out["error_code"] = (r.json().get("error") or {}).get("code")
        out["provider_received_fields"] = sorted(sent.keys()) if isinstance(sent, dict) else []
        out["provider_thinking_field"] = sent.get("thinking") if isinstance(sent, dict) else None
        out["provider_status"] = log[-1]["status"] if log else None
        print(json.dumps(out))

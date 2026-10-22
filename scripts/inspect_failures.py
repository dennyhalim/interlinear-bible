import json, sys, anthropic
n = 0
for item in anthropic.Anthropic().messages.batches.results(sys.argv[1]):
    if item.result.type != "succeeded":
        continue
    m = item.result.message
    text = "".join(b.text for b in m.content if b.type == "text").strip()
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        print(f"{item.custom_id} stop_reason={m.stop_reason} chars={len(text)} | {e}")
        print("   start:", repr(text[:120]))
        print("   error:", repr(text[max(0, e.pos - 100): e.pos + 60]))
        print("   end  :", repr(text[-120:]))
        n += 1
        if n >= int(sys.argv[2] if len(sys.argv) > 2 else 4):
            break

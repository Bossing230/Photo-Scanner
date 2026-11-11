"""Run:  py test_key.py
Checks the Gemini key and tries models with a real request until one works.
The working model is saved in gemini_model.txt and the app uses it automatically."""
import classify_api as c

key = c.api_key()
if not key:
    print("NO KEY FOUND. Create a file named gemini_key.txt in this folder and paste only the key inside.")
    raise SystemExit(1)
print(f"Key found (ends with ...{key[-4:]})")

for _ in range(12):
    try:
        name = c.pick_model()
    except Exception as err:
        print("FAILED to find a model:", str(err)[:200])
        raise SystemExit(1)
    try:
        reply = c.client().models.generate_content(model=name, contents="Reply with the single word OK")
        print(f"  WORKS   {name} -> {(reply.text or '').strip()[:20]!r}")
        c.remember(name)
        print(f"\nOK. Using '{name}' (saved in gemini_model.txt). Now run: py app.py")
        raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as err:
        print(f"  FAILED  {name} -> {str(err)[:150]}")
        if c.model_unusable(err):
            c.drop_model(name)      # try the next model
        else:
            print("\nThis is not a model problem. Copy the line above and send it to me.")
            raise SystemExit(1)
print("No model worked. Copy the FAILED lines above and send them to me.")
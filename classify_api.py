"""
Photo Organizer - AI category API (uses the Google Gemini API)

Endpoint
    POST /api/classify   (multipart/form-data)
        image : one photo (JPEG; the page shrinks it before sending)

    Returns JSON:  {"label": "Banana", "category": "Fruit", "confidence": 0.97}

The API key is read from the file gemini_key.txt (next to this file) or, if that does not
exist, from the GEMINI_API_KEY environment variable. It is never put in the web page.
Get a free key at https://aistudio.google.com (Get API key).

Models: the app finds a Gemini Flash model that works for your key by itself. A model that
Google refuses (for example "no longer available to new users") is skipped automatically and
the next one is tried. The working model is remembered in gemini_model.txt.

Setup:  pip install flask google-genai
"""

import json
import os
import re

from flask import Blueprint, jsonify, request
from google import genai
from google.genai import errors, types

CATEGORIES = [
    "Fruit", "Vegetable", "Meat", "Seafood", "Dairy", "Bakery", "Snack", "Spice", "Condiment",
    "Drink", "Dish", "Frozen", "Grain", "Egg", "Dessert", "Nuts", "Canned", "Food",
    "Animal", "Person", "Plant", "Flower", "Vehicle", "Building", "Landscape",
    "Electronics", "Appliance", "Furniture", "Clothing", "Shoes", "Bag", "Jewelry",
    "Kitchenware", "Tool", "Toy", "Sports", "Cosmetic", "Medicine", "Cleaning", "Stationery",
    "Book", "Document", "Other",
]
MAX_BYTES = 5 * 1024 * 1024

PROMPT = (
    "Look at this photo. Name its main subject in one to three words (for example "
    '"Banana" or "Golden retriever") and choose the single best category from this list: '
    + ", ".join(CATEGORIES)
    + '. Use "Spice" for spices, herbs and dried leaves, "Condiment" for sauces, oils and vinegar (also when bottled), '
    '"Meat" for raw meat, "Seafood" for fish and shellfish used as food, "Drink" for beverages, "Dish" for cooked meals, '
    '"Kitchenware" for pots, pans, plates and utensils, and "Food" only for other food. '
    'Fresh produce such as garlic, onions and ginger is "Vegetable". '
    '"Frozen" for ice, ice cubes, ice cream and frozen food, "Grain" for rice, noodles, pasta, flour and cereal, '
    '"Egg" for eggs, "Dessert" for sweets, cakes and candy, "Nuts" for nuts and seeds, '
    '"Canned" for canned goods, "Medicine" for pills and medical items, "Cleaning" for soap and cleaning products. '
    'Describe only what you can clearly see. If you are not sure, give a low confidence instead of guessing. '
    'Use "Other" only if nothing fits. Reply with JSON only, no other text: '
    '{"label": "<main subject>", "category": "<one category from the list>", "confidence": <number from 0 to 1>}'
)

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, "gemini_key.txt")
MODEL_FILE = os.path.join(HERE, "gemini_model.txt")

bp = Blueprint("classify_api", __name__)
_client = None


def _read(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return fh.read().strip().strip("\"'") or None
    except OSError:
        return None


def api_key():
    return _read(KEY_FILE) or (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip() or None


def client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=api_key())
    return _client


print("Gemini API key: " + ("found" if api_key() else "NOT FOUND - save it in gemini_key.txt next to app.py"))

# ------------------------------------------------------------------ models ----
_saved = _read(MODEL_FILE)
_model = os.environ.get("PHOTO_MODEL") or _saved   # first choice; may be replaced if it does not work
_bad = set()                                       # models Google refused for this key
_label_category = {}                               # label -> category, so the same thing is always filed the same way


def ranked_models():
    """Flash models this key can list, best first: stable before preview, Flash-Lite first, newest first."""
    found = []
    for m in client().models.list():
        acts = getattr(m, "supported_actions", None) or []
        name = m.name.split("/")[-1]
        if name in _bad or (acts and "generateContent" not in acts):
            continue
        if "flash" not in name or re.search(r"image|tts|live|audio|native|robotics|computer|embed|exp", name):
            continue
        version = max([float(v) for v in re.findall(r"\d+(?:\.\d+)?", name)] or [0])
        found.append((("preview" not in name), ("lite" in name), version, name))
    return [x[3] for x in sorted(found, reverse=True)]


def pick_model():
    global _model
    if _model and _model not in _bad:
        return _model
    names = ranked_models()
    if not names:
        raise RuntimeError("No usable Gemini Flash model was found for this key.")
    _model = names[0]
    print(f"Using Gemini model: {_model}")
    return _model


def drop_model(name):
    """Google refused this model: never use it again and let pick_model choose another."""
    global _model
    _bad.add(name)
    if _model == name:
        _model = None
    print(f"Model {name} cannot be used with this key, trying another one")


def model_unusable(err):
    text = str(err)
    code = getattr(err, "code", 0)
    return code == 404 or (code == 429 and "limit: 0" in text)


def remember(name):
    global _saved
    if name != _saved:
        try:
            with open(MODEL_FILE, "w", encoding="utf-8") as fh:
                fh.write(name)
            _saved = name
        except OSError:
            pass


def generate(data):
    last = None
    for _ in range(10):
        name = pick_model()
        try:
            resp = client().models.generate_content(
                model=name,
                contents=[types.Part.from_bytes(data=data, mime_type="image/jpeg"), PROMPT],
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
            )
            remember(name)
            return resp
        except errors.APIError as err:
            if model_unusable(err):
                last = err
                drop_model(name)
                continue
            raise
    raise last or RuntimeError("No Gemini model worked with this key.")


# --------------------------------------------------------------------- API ----
@bp.route("/api/classify", methods=["POST", "OPTIONS"])
def classify():
    if request.method == "OPTIONS":
        return ("", 204)
    if not api_key():
        return jsonify(error="No Gemini API key found. Save it in gemini_key.txt next to app.py, then restart app.py."), 503

    upload = request.files.get("image")
    data = upload.read() if upload else b""
    if not data:
        return jsonify(error="No image was sent."), 400
    if len(data) > MAX_BYTES:
        return jsonify(error="The image is larger than 5 MB."), 400

    try:
        text = generate(data).text or ""
    except errors.APIError as err:
        code = getattr(err, "code", 0)
        if code in (401, 403) or "API key" in str(err):
            return jsonify(error="The Gemini API key was rejected. Check the key in gemini_key.txt."), 503
        if code == 404:
            return jsonify(error=f"No Gemini model worked for this key. Google said: {str(err)[:160]}"), 503
        if code == 429:
            return jsonify(error="The free Gemini limit was reached. Wait a moment and try again."), 429
        return jsonify(error=f"The AI service returned an error: {str(err)[:160]}"), 502
    except Exception as err:  # network problems, no model found, blocked content...
        return jsonify(error=f"The AI request failed: {str(err)[:160]}"), 502

    found = re.search(r"\{.*\}", text, re.S)
    try:
        out = json.loads(found.group(0))
    except (AttributeError, ValueError):
        return jsonify(error="The AI answer could not be read."), 502

    category = out.get("category")
    try:
        confidence = max(0.0, min(1.0, float(out.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    label = str(out.get("label") or "Unknown").strip()[:40]
    category = category if category in CATEGORIES else "Other"
    if confidence < 0.35:
        category = "Other"            # not sure: do not guess a category
    elif category != "Other":
        category = _label_category.setdefault(label.lower(), category)   # same label, same category
    return jsonify(label=label, category=category, confidence=confidence)
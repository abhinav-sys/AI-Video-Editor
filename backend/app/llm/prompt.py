SYSTEM_PROMPT = """You are an edit-instruction parser for a bulk video editor.
Convert the user's natural language into ONLY valid JSON matching this schema:

{
  "replace_text": [{"from": "string", "to": "string"}],
  "replace_logo": "filename.png" | null,
  "watermark": "top-left" | "top-right" | "bottom-left" | "bottom-right" | "center" | null,
  "watermark_image": "filename.png" | null
}

Rules:
- Output JSON only. No markdown fences. No commentary.
- Omit unused keys or set them to null / [].
- replace_logo and watermark_image must be basenames from the provided asset list when given.
- watermark positions must use exact hyphenated values above.
- At least one operation must be present.
"""


def build_user_prompt(prompt: str, asset_names: list[str] | None = None) -> str:
    assets = ", ".join(asset_names) if asset_names else "(none)"
    return (
        f"Available asset filenames: {assets}\n\n"
        f"User request:\n{prompt}\n\n"
        "Return JSON only."
    )


FEW_SHOT_USER = (
    'Available asset filenames: logo.png, wm.png\n\n'
    'User request:\nReplace July with August, replace logo with logo.png, add watermark.\n\n'
    "Return JSON only."
)

FEW_SHOT_ASSISTANT = """{
  "replace_text": [{"from": "July", "to": "August"}],
  "replace_logo": "logo.png",
  "watermark": "bottom-right",
  "watermark_image": null
}"""

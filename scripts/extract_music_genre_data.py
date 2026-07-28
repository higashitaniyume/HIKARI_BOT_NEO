"""Extract the DATA object from music_genre index.html and output as JSON."""

import json
import re
import sys


def extract_data_from_html(filepath: str) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Find the DATA object
    match = re.search(r"const DATA\s*=\s*\{", content)
    if not match:
        raise ValueError("Could not find DATA declaration")

    start = match.end() - 1  # include opening brace
    depth = 0
    pos = start
    while pos < len(content):
        if content[pos] == "{":
            depth += 1
        elif content[pos] == "}":
            depth -= 1
            if depth == 0:
                end = pos + 1
                break
        pos += 1
    else:
        raise ValueError("Could not find end of DATA object")

    data_js = content[start:end]
    return _convert_js_to_json(data_js)


def _convert_js_to_json(js_text: str) -> dict:
    """Convert a JavaScript object literal to a Python dict."""
    # 1. Remove comments
    js_text = re.sub(r"/\*.*?\*/", "", js_text, flags=re.DOTALL)
    js_text = re.sub(r"//[^\n]*", "", js_text)

    # 2. Character-by-character processing: normalize strings, handle escapes
    result_chars = []
    i = 0
    in_str = False
    str_quote = None

    while i < len(js_text):
        ch = js_text[i]

        if in_str:
            if ch == "\\":
                i += 1
                if i < len(js_text):
                    next_ch = js_text[i]
                    if next_ch == "'":
                        # JS allows \' but JSON doesn't — just output the quote
                        result_chars.append("'")
                    elif next_ch == "\n":
                        # line continuation — skip both
                        pass
                    else:
                        result_chars.append("\\")
                        result_chars.append(next_ch)
                    i += 1
                continue
            elif ch == str_quote:
                in_str = False
                result_chars.append(ch)
                i += 1
                continue
            elif ch == "\n":
                # Escape newlines inside strings
                result_chars.append("\\n")
                i += 1
                continue
            else:
                result_chars.append(ch)
                i += 1
                continue
        else:
            if ch in ('"', "'"):
                in_str = True
                str_quote = '"' if ch == "'" else ch
                result_chars.append('"')  # normalize to double quote
                i += 1
                continue
            elif ch == "`":
                # template literal — treat as string with double quotes
                in_str = True
                str_quote = "`"
                result_chars.append('"')
                i += 1
                continue

        result_chars.append(ch)
        i += 1

    fixed = "".join(result_chars)

    # 3. Quote object keys: after {, ,, or newline (with optional whitespace),
    # then word chars, then :
    # Use a more robust regex approach
    # Match: word_chars: but not inside strings
    # Strategy: use re.sub with a function that checks context

    # First, let's try to split into parts and reconstruct
    # Simpler: match all patterns where a bare word is followed by :
    # and is preceded by {, ,, or whitespace

    result = []
    i = 0
    in_s = False
    sq = None

    while i < len(fixed):
        ch = fixed[i]

        if in_s:
            if ch == "\\":
                result.append(ch)
                i += 1
                if i < len(fixed):
                    result.append(fixed[i])
                    i += 1
                continue
            elif ch == sq:
                in_s = False
            result.append(ch)
            i += 1
            continue

        if ch == '"':
            in_s = True
            sq = '"'
            result.append(ch)
            i += 1
            continue

        # Check for unquoted object key
        # It should be preceded by: { or , or whitespace (that's after { or ,)
        # And consist of: word chars ($_a-zA-Z0-9) followed by :
        if ch in ("{", ",", "\n"):
            result.append(ch)
            i += 1
            # Consume whitespace
            ws = ""
            while i < len(fixed) and fixed[i] in " \t\r\n":
                ws += fixed[i]
                i += 1
            # Try to read a word
            word = ""
            w_start = i
            while i < len(fixed) and (fixed[i].isalnum() or fixed[i] in "$_"):
                word += fixed[i]
                i += 1
            if word and i < len(fixed) and fixed[i] == ":":
                # It's a key — quote it
                result.append(ws)
                result.append('"' + word + '"')
                # Don't consume the colon; next iteration handles it
                continue
            else:
                # Not a key — output what we consumed
                result.append(ws)
                result.append(word)
                continue

        result.append(ch)
        i += 1

    fixed2 = "".join(result)

    # 4. Remove trailing commas before } or ]
    fixed2 = re.sub(r",(\s*[}\]])", r"\1", fixed2)

    # 5. Fix backtick template strings that we converted to " but contain ${}
    # This is a sample fix — the data uses backtick strings with ${...} interpolation
    # We already converted ` to ", so ${...} becomes ${...} inside a string which is fine for JSON
    # as long as the content doesn't have unescaped quotes

    try:
        return json.loads(fixed2)
    except json.JSONDecodeError as e:
        print(f"JSON decode failed at position {e.pos}: {e.msg}", file=sys.stderr)
        ctx_start = max(0, e.pos - 200)
        ctx_end = min(len(fixed2), e.pos + 200)
        print(f"Context: ...{fixed2[ctx_start:ctx_end]}...", file=sys.stderr)
        raise


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract_music_genre_data.py <index.html> [output.json]", file=sys.stderr)
        sys.exit(1)

    html_path = sys.argv[1]
    data = extract_data_from_html(html_path)

    if len(sys.argv) >= 3:
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Written {len(json.dumps(data, ensure_ascii=False))} bytes to {sys.argv[2]}", file=sys.stderr)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))

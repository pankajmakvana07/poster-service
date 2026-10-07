# Poster Engine

Stateless FastAPI + Pillow service that renders posters from **any** template supplied by the API.
Nothing is hard-coded to one design: the template image URL, photo area, photo shape and 1-9 text
fields (position, colours, styling) all come from the request.

```
POST /api/v1/posters/prepare   download + analyze the template, return a signed renderContext + preview
POST /api/v1/posters/render    verify the context, place the photo, draw the fields, return base64
```

Generated posters are never stored. The downloaded template image is kept only as a short-lived
temporary cache entry (see "Temporary template storage").

## 1. Installation

Python 3.11+.

```
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then edit
```

## 2. Environment variables

| variable | default | meaning |
|---|---|---|
| APP_ENV | development | `production` hides internal error details and requires SECRET_KEY |
| SECRET_KEY | (random per process) | HMAC key that signs render contexts. **Set it in production**, identical on every worker/server |
| ALLOWED_TEMPLATE_HOSTS | cdn.simpliworks.com | comma separated allowlist; `*.example.com` wildcards allowed, `*` allows any public host |
| ALLOW_HTTP_TEMPLATES | false | allow `http://` template URLs (dev only) |
| ALLOW_PRIVATE_HOSTS | false | disables the private-IP check (dev/tests only) |
| DOWNLOAD_TIMEOUT_SECONDS | 10 | template download timeout |
| MAX_TEMPLATE_BYTES / MAX_TEMPLATE_PIXELS | 15 MB / 25 MP | template limits |
| MAX_PHOTO_BYTES / MAX_PHOTO_PIXELS | 12 MB / 40 MP | decoded photo limits |
| MAX_REQUEST_BYTES | 20 MB | request body limit |
| RENDER_CONTEXT_TTL_SECONDS | 86400 | how long a prepared context stays valid |
| TEMPLATE_CACHE_BACKEND | disk | `disk`, `memory` or `none` |
| TEMPLATE_CACHE_DIR | system temp / poster-engine-cache | disk cache folder |
| TEMPLATE_CACHE_TTL_SECONDS | 1800 | cache entry lifetime |
| TEMPLATE_CACHE_MAX_ENTRIES | 32 | cache size bound |
| DEFAULT_FONT_FILE | default-font.ttf | default font inside `app/fonts` |
| MIN_FONT_SIZE_PX / MAX_FONT_SIZE_PX | 10 / 400 | global font limits (fields can narrow them) |
| DEFAULT_PLACEHOLDER_TOLERANCE | 24 | colour tolerance when the template does not specify one |
| OUTPUT_JPEG_QUALITY | 92 | default JPEG quality |
| PREVIEW_MAX_WIDTH | 720 | width of the preview returned by /prepare |
| CORS_ORIGINS | * | comma separated origins |

## 3. Run

```
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Swagger UI: http://localhost:8000/docs. Tests: `python -m pytest -q`.
Docker: `docker build -t poster-engine . && docker run -p 8000:8000 -e SECRET_KEY=... poster-engine`.

Offline demo with the bundled sample template: `python scripts/demo_local.py [photo.jpg]` writes
`output/preview.jpg` and `output/poster.jpg`.

Local development against your own files: serve a folder with `python -m http.server 8765` and start
the API with `ALLOWED_TEMPLATE_HOSTS=localhost ALLOW_PRIVATE_HOSTS=true ALLOW_HTTP_TEMPLATES=true`
(never in production).

## 4. Flow

1. `/prepare`: validates the definition, downloads the template (SSRF-safe), analyzes it, finds the photo
   placeholder, and returns `renderContext` (HMAC signed) + `templateFingerprint` + a preview.
2. `/render`: send `templateId`, `renderContext`, the base64 photo and the field values. The service verifies
   the signature, expiry, template id/URL/fingerprint and the template image hash, then renders.

Template definition (sent to `/prepare`) and runtime data (sent to `/render`) are separate. All
coordinates, shape and styling are frozen inside the signed context, so a client cannot tamper with them.

## 5. `/prepare` request

```json
{
  "templateId": "TPL_BDAY_001",
  "templateName": "Dealer Birthday Gold Banner",
  "categoryId": "CAT_DEALER",
  "templateImageUrl": "https://cdn.simpliworks.com/templates/bday_gold.jpeg",
  "photoArea": {
    "fieldkey": "photo_main", "fieldvalue": null,
    "x": 0.25, "y": 0.32, "width": 0.5, "height": 0.33,
    "shape": "hexagon"
  },
  "fields": [
    {"fieldkey": "field1", "fieldvalue": null, "x": 0.15, "y": 0.764, "width": 0.72, "height": 0.05,
     "textColor": "#FFFFFF", "backgroundColor": "#C99A32"},
    {"fieldkey": "field2", "fieldvalue": null, "x": 0.28, "y": 0.843, "width": 0.44, "height": 0.034,
     "textColor": "#FFFFFF", "backgroundColor": "#C99A32"}
  ]
}
```

Optional top level: `coordinateMode` (`normalized` default | `pixel`), `includePreview` (true), `previewGuides` (false).

Optional `photoArea` options:

| option | default | notes |
|---|---|---|
| shape | placeholder | `placeholder`, `rectangle`, `circle`, `rounded_rectangle`, `hexagon` |
| fit | cover | `cover` (fill and crop) or `contain` (letterbox with `backgroundColor`) |
| focusX / focusY | 0.5 / 0.5 | which part of the photo is kept when cropping (0..1) |
| cornerRadius | 0.12 | rounded rectangle radius as a fraction of the shorter side (0..0.5) |
| hexagonOrientation | pointy | `pointy` (vertex on top) or `flat` |
| backgroundColor | #FFFFFF | letterbox colour for `contain` |
| placeholderColor | auto | e.g. `#FF00FF`. If omitted it is detected inside `photoArea` |
| placeholderColorTolerance | 24 | max per-channel difference (0..128) |
| placeholderMode | detect | `detect` (error if missing), `auto` (fall back to coordinates), `coordinates` (no detection) |

Optional per field: `align` (left/center/right), `verticalAlign` (top/center/bottom), `padding` (0..0.4 of the
shorter side, default 0.1), `maxFontSize`, `minFontSize`, `wrap` (true), `maxLines`, `overflow`
(`error` default or `ellipsis`), `fontName` (a file in `app/fonts`, e.g. `regular-font.ttf`).

Response (abridged):

```json
{
  "success": true,
  "templateId": "TPL_BDAY_001",
  "templateFingerprint": "d618affd...",
  "imageWidth": 1814, "imageHeight": 2560,
  "photoRegion": {"x": 0.2773, "y": 0.2832, "width": 0.4454, "height": 0.368,
                  "pixelX": 503, "pixelY": 725, "pixelWidth": 808, "pixelHeight": 942,
                  "detected": true, "candidates": 4, "placeholderColor": "#FDFEFF",
                  "placeholderColorTolerance": 24, "shape": "placeholder", "fit": "cover"},
  "fields": [{"fieldkey": "field1", "x": 0.15, "...": "..."}],
  "renderContext": "eyJ...signature",
  "renderContextExpiresAt": 1790916405,
  "previewFormat": "jpeg", "previewBase64": "/9j/4AAQ...",
  "warnings": []
}
```

## 6. `/render` request

```json
{
  "templateId": "TPL_BDAY_001",
  "templateImageUrl": "https://cdn.simpliworks.com/templates/bday_gold.jpeg",
  "renderContext": "<from /prepare>",
  "photoArea": {"fieldkey": "photo_main", "fieldvalue": "<BASE64_IMAGE>"},
  "fields": [
    {"fieldkey": "field1", "fieldvalue": "Happy Birthday John"},
    {"fieldkey": "field2", "fieldvalue": "ABC Motors", "textColor": "#000000", "backgroundColor": "#FFFFFF"}
  ],
  "outputFormat": "jpeg"
}
```

* Only the fields you list are drawn (1 to 9, keys must exist in the prepared template, values must not be empty).
* `textColor` / `backgroundColor` are optional runtime overrides; otherwise the prepared colours are used.
* `templateImageUrl`, `templateFingerprint`, and field/photo coordinates are optional; if present they must match.
* `photoBase64` is accepted as an alias for `photoArea.fieldvalue`.
* `outputFormat`: `jpeg` (default), `png`, `webp`; `quality` 30-100.

```json
{"success": true, "templateId": "TPL_BDAY_001", "templateFingerprint": "d618affd...",
 "imageFormat": "jpeg", "mimeType": "image/jpeg", "width": 1814, "height": 2560,
 "imageBase64": "/9j/4AAQ...", "warnings": []}
```

Errors are always `{"success": false, "error": "CODE", "message": "...", "details": [...]}` with codes such as
`INVALID_FIELD_COUNT`, `INVALID_COORDINATES`, `INVALID_COLOR`, `INVALID_PHOTO_SHAPE`, `TEMPLATE_URL_NOT_ALLOWED`,
`TEMPLATE_DOWNLOAD_FAILED`, `INVALID_TEMPLATE_IMAGE`, `PLACEHOLDER_NOT_FOUND`, `INVALID_BASE64`, `INVALID_PHOTO`,
`MISSING_PHOTO`, `TEMPLATE_MISMATCH` (409), `INVALID_RENDER_CONTEXT`, `RENDER_CONTEXT_EXPIRED` (410),
`TEXT_DOES_NOT_FIT`, `TEXT_TOO_LONG`.

## 7. Example curl

```
curl -s -X POST http://localhost:8000/api/v1/posters/prepare \
  -H 'Content-Type: application/json' -d @samples/prepare_request.json > prepared.json

python - <<'PY'
import json, base64
p = json.load(open('prepared.json')); r = json.load(open('samples/render_request.json'))
r['renderContext'] = p['renderContext']
r['photoArea']['fieldvalue'] = base64.b64encode(open('samples/portrait.jpg','rb').read()).decode()
json.dump(r, open('render.json', 'w'))
PY

curl -s -X POST http://localhost:8000/api/v1/posters/render \
  -H 'Content-Type: application/json' -d @render.json | python -c "import sys,json,base64; open('poster.jpg','wb').write(base64.b64decode(json.load(sys.stdin)['imageBase64']))"
```

## 8. Base64 photo

Raw base64 or a data URI (`data:image/jpeg;base64,...`, also png/webp). Whitespace and missing padding are
tolerated; URL-safe base64 works. JPEG, PNG and WEBP only. EXIF rotation is applied. Transparency is flattened
onto white. Limits come from MAX_PHOTO_BYTES / MAX_PHOTO_PIXELS.

## 9. Coordinate system

All `x, y, width, height` are fractions (0..1) of the template, origin top-left, `x`/`y` is the top-left corner:
`pixel = round(value * template_size)`. The same JSON therefore works for 1080x1350 and 2000x2500 templates.
`coordinateMode: "pixel"` accepts pixel values instead and converts them to fractions after the template is loaded.

## 10. Photo area, placeholder detection and shapes

The photo placeholder is a flat colour in the template. Detection:

1. Placeholder colour = `placeholderColor`, or the dominant flat colour in the middle of `photoArea`.
2. Pixels within `placeholderColorTolerance` of that colour are marked (tolerates JPEG noise); the search is
   limited to `photoArea` plus a margin so unrelated pixels elsewhere are ignored.
3. Small noise is cleaned, connected components are labelled, and the component overlapping `photoArea` the
   most is chosen (other same-coloured blobs are ignored); holes are filled.
4. The component's bounding box is the placement; its exact pixels are the mask for `shape: "placeholder"`.

Shapes decide HOW the photo is masked:

* `placeholder` (default): use the detected pixels. Best for frames (e.g. a metal hexagon) because the frame
  stays on top untouched.
* `rectangle`, `rounded_rectangle`, `hexagon`: drawn inside the detected box with anti-aliased masks.
* `circle`: a true circle (diameter = shorter side) centred in the box.

For explicit shapes, leftover placeholder-coloured pixels outside the shape are repainted with the average colour
of the pixels surrounding the placeholder. The photo is never stretched: `cover` scales proportionally and crops
(centre by default; small photos are enlarged and a warning is returned above 3x), `contain` letterboxes.

## 11. Colours

`#RGB`, `#RRGGBB`, `#RRGGBBAA`, `#RGBA` (with or without `#`) and `transparent`. Alpha is honoured for both
background and text. Invalid values return `INVALID_COLOR`. A missing `backgroundColor` means no background.

## 12. Text fitting

Per field: padded box, then the largest font size (binary search between min and max) at which the greedy
word-wrapped text fits inside the box. Short text stays big, long text shrinks, then wraps onto more lines if
the box is tall enough. Words longer than the box are split. Explicit `\n` is honoured. Centre alignment
is the default; left/right and top/bottom are supported. If the text cannot fit at the minimum size the
request fails with `TEXT_DOES_NOT_FIT`, or is shortened with an ellipsis if the field sets `overflow: "ellipsis"`
(a warning is returned). The bundled font is `app/fonts/default-font.ttf` (Liberation Sans Bold, SIL OFL);
`regular-font.ttf` is also included. Add more `.ttf/.otf` files to `app/fonts` and select them with `fontName`.

## 13. Template identity

`templateFingerprint` = SHA-256 over templateId, the SHA-256 of the template image bytes, the image size, the
photo configuration and every field definition. At render time the signed context, `templateId`, optional
`templateImageUrl`/`templateFingerprint`, any supplied coordinates, and the hash of the (cached or re-downloaded)
template image must all agree, otherwise `TEMPLATE_MISMATCH` (409) is returned. A different template is never
rendered silently.

## 14. Temporary template storage

The downloaded template is cached under its SHA-256 for `TEMPLATE_CACHE_TTL_SECONDS` (default 30 min) so
`/render` does not download it again. `disk` (default) shares the cache between worker processes, is purged
automatically (TTL and max entries) and is cleared on shutdown; `memory` is per process and size bounded;
`none` disables it. If the cache entry is gone, `/render` re-downloads the URL and verifies the hash against the
signed context. Posters and photos are never written to disk.

## 15. Security

* SSRF: https only (http opt-in), no credentials in URLs, default ports only, host allowlist, localhost/internal
  names blocked, every resolved IP must be public, redirects re-validated (max 3), download timeout and size cap.
  Residual risk: DNS can change between validation and connection; keep the allowlist to your CDN and add network egress rules.
* Signed render context (HMAC-SHA256) with expiry; use one SECRET_KEY across all instances.
* Pillow decompression-bomb protection, pixel and byte limits, format whitelist, strict base64 validation.
* Fonts are resolved by file name inside `app/fonts` only. No stack traces are returned in production.
* There is no authentication or rate limiting; put the service behind your gateway.

## 16. Assumptions

* Coordinates are fractions with a top-left origin; `x`/`y` is the top-left corner of the box.
* `/render` requires a non-empty value for each listed field; omit a field to skip it.
* Photo shape is explicit metadata; colour detection only decides where. A missing `shape` means "use the detected pixels".
* If detection fails the default behaviour is an error (`PLACEHOLDER_NOT_FOUND`) so misconfigured templates are
  noticed early; use `placeholderMode: "auto"` for a coordinate fallback.
* Transparent template images are flattened onto white.
* Complex scripts (e.g. Devanagari) need a font that supports them; add one to `app/fonts` and use `fontName`.
* Output is JPEG by default (no transparency needed).

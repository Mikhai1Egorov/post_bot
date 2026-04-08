# EXCEL_CONSTANTS

REQUIRED_FIELDS
- channel
- topic
- keywords
- time_range
- response_language
- mode

OPTIONAL_FIELDS
- title
- search_language
- style
- length
- include_image
- footer_text
- footer_link
- schedule_at

ALL_FIELDS
- channel
- topic
- title
- keywords
- time_range
- search_language
- response_language
- style
- length
- include_image
- footer_text
- footer_link
- schedule_at
- mode

# =========================
# ENUM VALUES
# =========================

TIME_RANGE_VALUES
- 24h
- 3d
- 7d
- 30d

SEARCH_LANGUAGE_VALUES
- en
- ru
- uk
- es
- zh
- hi
- ar

RESPONSE_LANGUAGE_VALUES
- en
- ru
- uk
- es
- zh
- hi
- ar

STYLE_VALUES
- journalistic
- simple
- expert

LENGTH_VALUES
- short
- medium
- long

INCLUDE_IMAGE_VALUES
- TRUE
- FALSE

MODE_VALUES
- instant
- approval

# =========================
# DEFAULT BEHAVIOR
# =========================

title
→ if empty:
   auto-generate from topic

search_language
→ if empty:
   use response_language

style
→ if empty:
   use "journalistic"

length
→ if empty:
   use "medium"

include_image
→ if empty:
   use FALSE

footer_text
→ if empty:
   footer block is NOT included

footer_link
→ if empty:
   if footer_text exists → render text only
   if footer_text does not exist → footer block is NOT included

schedule_at
→ if empty:
   publish immediately (no scheduling)

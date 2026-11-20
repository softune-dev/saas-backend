"""Per-store look: a readable Google Fonts pair (no two stores share a font)
and a high-contrast palette. Storefront copy is English (catalog.py).

Colour roles, as the themes use them:
  primary  buttons, badges, links
  accent   headings and product names in themes 1 and 3, so it must be
           dark enough to read on the surface colour
  surface  page background
"""

LOOK = {
    "noor":      {"fonts": {"display": "Cormorant Garamond", "body": "Jost"},
                  "colors": {"primary": "#2B2420", "accent": "#2B2420", "surface": "#F6F1EA"}},
    "swarna":    {"fonts": {"display": "Cinzel", "body": "Lato"},
                  "colors": {"primary": "#7A5A1E", "accent": "#3A2A12", "surface": "#FBF7EF"}},
    "oudh":      {"fonts": {"display": "Bodoni Moda", "body": "Montserrat"},
                  "colors": {"primary": "#1C1714", "accent": "#1C1714", "surface": "#F4EFE8"}},
    "voltix":    {"fonts": {"display": "Plus Jakarta Sans", "body": "Inter"},
                  "colors": {"primary": "#2563EB", "accent": "#0F172A", "surface": "#F5F7FB"}},
    "freshkart": {"fonts": {"display": "Outfit", "body": "Nunito Sans"},
                  "colors": {"primary": "#15803D", "accent": "#14532D", "surface": "#F4FAF3"}},
    "tinytots":  {"fonts": {"display": "Fredoka", "body": "Nunito"},
                  "colors": {"primary": "#EA580C", "accent": "#4C1D95", "surface": "#FFF8F1"}},
    "ghorkona":  {"fonts": {"display": "Fraunces", "body": "DM Sans"},
                  "colors": {"primary": "#9A3412", "accent": "#292524", "surface": "#FAF6F0"}},
    "pawpal":    {"fonts": {"display": "Baloo 2", "body": "Mulish"},
                  "colors": {"primary": "#0F766E", "accent": "#134E4A", "surface": "#FFF9F0"}},
    "raaj":      {"fonts": {"display": "Playfair Display", "body": "Source Sans 3"},
                  "colors": {"primary": "#7F1D1D", "accent": "#3F1515", "surface": "#F8F4EE"}},
    "stride":    {"fonts": {"display": "Anton", "body": "Barlow"},
                  "colors": {"primary": "#111827", "accent": "#111827", "surface": "#F5F5F4"}},
}

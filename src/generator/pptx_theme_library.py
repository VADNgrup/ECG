from __future__ import annotations

# Hand-curated deck-theme library: 20 COHESIVE visual identities, each a full 11-field palette + a
# tasteful font trio, tagged by subject-matter genre. Palette values are drawn from established design
# systems (Tailwind/Radix/IBM Carbon scales) so accent/bg/text stay harmonious and readable rather than
# invented ad-hoc. `_call_design_spec_llm` shows the LLM name+tags+description and it picks a matching
# name; `_validate_and_fill_design_spec` loads the whole palette verbatim.
#
# Contrast contract (verified by the palette check in the round-7 plan): text_light on bg_light and
# text_dark on bg_dark are >= WCAG AA (4.5). c1..c4 are a harmonious 4-step set used as card fills;
# text placed on them is chosen adaptively by deck_pptx_gen.js `textOn()` (dark ink on light steps,
# white on dark steps), so the steps only need to look good together, not all be dark.
#
# Dark themes use a soft (not pure-black) background and off-white (not pure-white) text with an accent
# bright enough to read but not glaring -- picked specifically to be easy on the eyes.
#
# Every theme MUST declare all 11 palette keys: bg_dark, bg_light, accent, c1, c2, c3, c4, text_dark,
# text_light, dim_dark, dim_light.

# SYSTEM fonts that ship with Windows/Microsoft Office -- chosen over Google Fonts (Inter/Fraunces/...)
# because those are NOT installed on typical machines: PowerPoint then substitutes them, which both
# discards the intended typeface AND triggers a word-doubling render artifact under normAutofit. These
# all render correctly wherever Office is installed.
SAFE_FONTS: dict[str, list[str]] = {
    'display': ['Georgia', 'Cambria', 'Constantia', 'Palatino Linotype', 'Book Antiqua', 'Rockwell'],
    'body':    ['Calibri', 'Segoe UI', 'Corbel', 'Candara', 'Century Gothic', 'Franklin Gothic Book', 'Gill Sans MT'],
    'mono':    ['Consolas', 'Courier New', 'Lucida Console'],
}


def _t(name, tags, desc, mode, pal, fonts):
    return {'name': name, 'tags': tags, 'description': desc, 'default_mode': mode,
            'palette': pal, 'fonts': fonts}


THEME_LIBRARY: list[dict] = [
    _t('Azure Professional', ['technology', 'business', 'science', 'engineering'],
       'Clean cool blue on near-white -- crisp, trustworthy, modern tech/business.', 'light',
       {'bg_light': 'F5F8FC', 'bg_dark': '0E1726', 'accent': '2563EB',
        'c1': '1E3A8A', 'c2': '3B82F6', 'c3': '60A5FA', 'c4': 'BFDBFE',
        'text_light': '0F1B2D', 'text_dark': 'ECF2FB', 'dim_light': '5B6B7F', 'dim_dark': '94A7BE'},
       {'display': 'Cambria', 'body': 'Segoe UI', 'mono': 'Consolas'}),

    _t('Terracotta Editorial', ['history', 'humanities', 'arts', 'anthropology'],
       'Warm cream and burnt orange -- archival, literary, editorial warmth.', 'light',
       {'bg_light': 'FAF4EC', 'bg_dark': '2A1D14', 'accent': 'C05621',
        'c1': '7C2D12', 'c2': 'C2410C', 'c3': 'EA8C55', 'c4': 'F5D0B0',
        'text_light': '2A1E14', 'text_dark': 'F3E7D8', 'dim_light': '6E5A46', 'dim_dark': 'BBA588'},
       {'display': 'Palatino Linotype', 'body': 'Corbel', 'mono': 'Consolas'}),

    _t('Forest Botanical', ['biology', 'environment', 'ecology', 'agriculture'],
       'Fresh deep greens on white -- natural sciences, ecology, growth.', 'light',
       {'bg_light': 'F4F8F1', 'bg_dark': '0F1E14', 'accent': '15803D',
        'c1': '14532D', 'c2': '16A34A', 'c3': '4ADE80', 'c4': 'BBF7D0',
        'text_light': '12251A', 'text_dark': 'E9F4EC', 'dim_light': '566B5C', 'dim_dark': '9DB6A4'},
       {'display': 'Rockwell', 'body': 'Century Gothic', 'mono': 'Consolas'}),

    _t('Crimson Bold', ['journalism', 'rhetoric', 'literature', 'media'],
       'Stark white with a decisive red -- bold editorial, high-impact.', 'light',
       {'bg_light': 'FFFFFF', 'bg_dark': '171214', 'accent': 'DC2626',
        'c1': '7F1D1D', 'c2': 'DC2626', 'c3': 'F87171', 'c4': 'FECACA',
        'text_light': '141414', 'text_dark': 'F5F0F0', 'dim_light': '6B6B6B', 'dim_dark': 'ABABAB'},
       {'display': 'Georgia', 'body': 'Corbel', 'mono': 'Courier New'}),

    _t('Violet Creative', ['design', 'creative', 'media', 'innovation'],
       'Soft white with vivid violet -- creative, design-forward, contemporary.', 'light',
       {'bg_light': 'F8F7FE', 'bg_dark': '17132A', 'accent': '7C3AED',
        'c1': '4C1D95', 'c2': '7C3AED', 'c3': 'A78BFA', 'c4': 'DDD6FE',
        'text_light': '1A1430', 'text_dark': 'F1EDFB', 'dim_light': '5F5878', 'dim_dark': 'A79FC4'},
       {'display': 'Cambria', 'body': 'Segoe UI', 'mono': 'Consolas'}),

    _t('Slate Corporate', ['business', 'management', 'operations', 'consulting'],
       'Neutral slate with a teal accent -- calm, professional, corporate.', 'light',
       {'bg_light': 'F6F7F9', 'bg_dark': '16191F', 'accent': '0EA5A5',
        'c1': '1E293B', 'c2': '475569', 'c3': '94A3B8', 'c4': 'CBD5E1',
        'text_light': '15181E', 'text_dark': 'EEF1F5', 'dim_light': '5A6674', 'dim_dark': '9AA6B4'},
       {'display': 'Rockwell', 'body': 'Calibri', 'mono': 'Consolas'}),

    _t('Amber Academic', ['law', 'political-science', 'academic', 'economics'],
       'Warm ivory with amber-gold -- formal, scholarly, considered.', 'light',
       {'bg_light': 'FBF7EF', 'bg_dark': '1C1710', 'accent': 'B45309',
        'c1': '78350F', 'c2': 'B45309', 'c3': 'D97706', 'c4': 'FCD34D',
        'text_light': '1F1A12', 'text_dark': 'F4EEE1', 'dim_light': '6B5D45', 'dim_dark': 'BBA987'},
       {'display': 'Georgia', 'body': 'Corbel', 'mono': 'Consolas'}),

    _t('Rose Soft', ['psychology', 'wellness', 'education', 'social'],
       'Gentle blush with rose-magenta -- warm, human, approachable.', 'light',
       {'bg_light': 'FDF6F7', 'bg_dark': '251820', 'accent': 'DB2777',
        'c1': '831843', 'c2': 'DB2777', 'c3': 'F472B6', 'c4': 'FBCFE8',
        'text_light': '241A1E', 'text_dark': 'F6EBEF', 'dim_light': '6E5960', 'dim_dark': 'B79AA1'},
       {'display': 'Book Antiqua', 'body': 'Candara', 'mono': 'Courier New'}),

    _t('Ocean Teal', ['medicine', 'health', 'marine', 'science'],
       'Clean aqua on pale cyan -- clinical, fresh, health & life sciences.', 'light',
       {'bg_light': 'F1FAFA', 'bg_dark': '0C2023', 'accent': '0891B2',
        'c1': '155E63', 'c2': '0E7490', 'c3': '22D3EE', 'c4': 'A5F3FC',
        'text_light': '0E1F21', 'text_dark': 'E7F6F7', 'dim_light': '4E6669', 'dim_dark': '92B0B3'},
       {'display': 'Cambria', 'body': 'Century Gothic', 'mono': 'Consolas'}),

    _t('Indigo Data', ['mathematics', 'statistics', 'data-science', 'computer-science'],
       'Crisp white with deep indigo -- analytical, precise, data-driven.', 'light',
       {'bg_light': 'F7F8FD', 'bg_dark': '12152B', 'accent': '4F46E5',
        'c1': '312E81', 'c2': '4F46E5', 'c3': '818CF8', 'c4': 'C7D2FE',
        'text_light': '14162B', 'text_dark': 'EEF0FB', 'dim_light': '585C78', 'dim_dark': 'A6ABC9'},
       {'display': 'Cambria', 'body': 'Segoe UI', 'mono': 'Consolas'}),

    _t('Sunset Coral', ['marketing', 'startup', 'product', 'innovation'],
       'Warm off-white with coral-orange -- energetic, friendly, product.', 'light',
       {'bg_light': 'FFF7F2', 'bg_dark': '261611', 'accent': 'EA580C',
        'c1': '9A3412', 'c2': 'EA580C', 'c3': 'FB923C', 'c4': 'FED7AA',
        'text_light': '241610', 'text_dark': 'F7ECE4', 'dim_light': '6E574A', 'dim_dark': 'BFA290'},
       {'display': 'Cambria', 'body': 'Franklin Gothic Book', 'mono': 'Consolas'}),

    _t('Emerald Fresh', ['sustainability', 'agriculture', 'environment', 'energy'],
       'Bright mint with emerald -- clean, sustainable, forward-looking.', 'light',
       {'bg_light': 'F2FBF6', 'bg_dark': '0C2018', 'accent': '059669',
        'c1': '065F46', 'c2': '059669', 'c3': '34D399', 'c4': 'A7F3D0',
        'text_light': '0F211A', 'text_dark': 'E7F7EF', 'dim_light': '4F6A5D', 'dim_dark': '92B4A4'},
       {'display': 'Rockwell', 'body': 'Century Gothic', 'mono': 'Consolas'}),

    _t('Plum Wine', ['literature', 'philosophy', 'linguistics', 'humanities'],
       'Soft ivory with deep wine -- literary, reflective, elegant.', 'light',
       {'bg_light': 'FAF6F8', 'bg_dark': '21141C', 'accent': '9D174D',
        'c1': '500724', 'c2': '9D174D', 'c3': 'D6438A', 'c4': 'F0C2DA',
        'text_light': '20141B', 'text_dark': 'F3E9EE', 'dim_light': '6A545F', 'dim_dark': 'B79AA6'},
       {'display': 'Palatino Linotype', 'body': 'Corbel', 'mono': 'Consolas'}),

    _t('Steel Minimal', ['engineering', 'architecture', 'minimal', 'technical'],
       'Cool gray minimalism with a single blue accent -- restrained, precise.', 'light',
       {'bg_light': 'F5F6F8', 'bg_dark': '15181D', 'accent': '3B82F6',
        'c1': '334155', 'c2': '64748B', 'c3': '94A3B8', 'c4': 'CBD5E1',
        'text_light': '171A1F', 'text_dark': 'EEF0F3', 'dim_light': '5C6672', 'dim_dark': '9BA5B1'},
       {'display': 'Cambria', 'body': 'Calibri', 'mono': 'Consolas'}),

    _t('Sky Clean', ['education', 'general', 'communication', 'social'],
       'Airy pale blue -- open, friendly, easy to read for teaching decks.', 'light',
       {'bg_light': 'F7FAFD', 'bg_dark': '111C2B', 'accent': '0284C7',
        'c1': '0C4A6E', 'c2': '0284C7', 'c3': '38BDF8', 'c4': 'BAE6FD',
        'text_light': '12202E', 'text_dark': 'EAF2FA', 'dim_light': '55697D', 'dim_dark': '93A9BE'},
       {'display': 'Rockwell', 'body': 'Candara', 'mono': 'Consolas'}),

    _t('Olive Muted', ['sociology', 'humanities', 'history', 'anthropology'],
       'Earthy off-white with muted olive -- grounded, humane, understated.', 'light',
       {'bg_light': 'F7F7F0', 'bg_dark': '1B1C13', 'accent': '4D7C0F',
        'c1': '3F4A1E', 'c2': '65803A', 'c3': 'A3B564', 'c4': 'D9E0B0',
        'text_light': '1E2015', 'text_dark': 'EFF0E4', 'dim_light': '5F6248', 'dim_dark': 'A3A98C'},
       {'display': 'Rockwell', 'body': 'Century Gothic', 'mono': 'Consolas'}),

    # ---- more light themes (2026 research: jewel-tone accents on warm neutrals, editorial calm) ----
    _t('Burgundy Wine', ['law', 'finance', 'luxury', 'history'],
       'Warm cream with a deep wine-red -- refined, authoritative, editorial luxury.', 'light',
       {'bg_light': 'FBF5F1', 'bg_dark': '241318', 'accent': '9B2242',
        'c1': '6B1730', 'c2': '9B2242', 'c3': 'C75B76', 'c4': 'F0C4CE',
        'text_light': '2A1218', 'text_dark': 'F3E4E8', 'dim_light': '6E4A52', 'dim_dark': 'C0A3AA'},
       {'display': 'Georgia', 'body': 'Corbel', 'mono': 'Courier New'}),

    _t('Sage Editorial', ['wellness', 'design', 'lifestyle', 'biology'],
       'Soft off-white with a calm sage green -- natural, understated, premium editorial.', 'light',
       {'bg_light': 'F3F6F1', 'bg_dark': '161C18', 'accent': '4F7A66',
        'c1': '2F4A3C', 'c2': '4F7A66', 'c3': '85A996', 'c4': 'CFE0D5',
        'text_light': '1C241F', 'text_dark': 'E9F0EB', 'dim_light': '5C6B60', 'dim_dark': '9DB0A4'},
       {'display': 'Palatino Linotype', 'body': 'Gill Sans MT', 'mono': 'Consolas'}),

    _t('Dusty Rose', ['fashion', 'arts', 'social', 'psychology'],
       'Muted rose on soft blush with charcoal ink -- warm, tasteful, design-forward.', 'light',
       {'bg_light': 'FAF3F4', 'bg_dark': '221518', 'accent': 'A8556A',
        'c1': '6E2E3D', 'c2': 'A8556A', 'c3': 'CE8B99', 'c4': 'EFD3D9',
        'text_light': '2A1A1E', 'text_dark': 'F1E5E8', 'dim_light': '71575C', 'dim_dark': 'BCA4A9'},
       {'display': 'Book Antiqua', 'body': 'Candara', 'mono': 'Consolas'}),

    _t('Lavender Mist', ['humanities', 'education', 'social', 'philosophy'],
       'Pale lavender with a muted violet -- gentle, literary, contemporary.', 'light',
       {'bg_light': 'F4F2FB', 'bg_dark': '1A1726', 'accent': '6D5DA6',
        'c1': '3F356B', 'c2': '6D5DA6', 'c3': '9E92C8', 'c4': 'DAD3EC',
        'text_light': '201C33', 'text_dark': 'ECE9F5', 'dim_light': '5F5878', 'dim_dark': 'ABA3C0'},
       {'display': 'Constantia', 'body': 'Segoe UI', 'mono': 'Consolas'}),

    _t('Turquoise Fresh', ['startup', 'health', 'marine', 'technology'],
       'Crisp off-white with a fresh green-teal turquoise -- energetic yet calm, modern.', 'light',
       {'bg_light': 'F0FAF7', 'bg_dark': '0B1F1B', 'accent': '16A085',
        'c1': '0E5A4C', 'c2': '16A085', 'c3': '57C2AC', 'c4': 'B7E6DB',
        'text_light': '10251F', 'text_dark': 'E3F4EF', 'dim_light': '4E6B64', 'dim_dark': '8FB8AE'},
       {'display': 'Cambria', 'body': 'Century Gothic', 'mono': 'Consolas'}),

    _t('Mustard Ochre', ['craft', 'history', 'editorial', 'journalism'],
       'Warm sand with a deep ochre gold -- grounded, vintage-editorial, characterful.', 'light',
       {'bg_light': 'FAF6EC', 'bg_dark': '201A0E', 'accent': 'A16207',
        'c1': '713F12', 'c2': 'A16207', 'c3': 'CA9A3C', 'c4': 'EEDFB0',
        'text_light': '241C0E', 'text_dark': 'F2E9D4', 'dim_light': '6E5B3C', 'dim_dark': 'BCA980'},
       {'display': 'Rockwell', 'body': 'Franklin Gothic Book', 'mono': 'Consolas'}),

    # ---- dark themes (soft bg, off-white text, calibrated for readability) ----
    _t('Midnight Navy', ['technology', 'physics', 'astronomy', 'engineering'],
       'Deep navy with a soft sky-blue accent -- modern, focused, easy on the eyes.', 'dark',
       {'bg_dark': '0F1729', 'bg_light': 'F4F7FC', 'accent': '38BDF8',
        'c1': '1E293B', 'c2': '334E68', 'c3': '5B7DA6', 'c4': '93B4D6',
        'text_dark': 'E8EEF6', 'text_light': '0F1729', 'dim_dark': '94A3B8', 'dim_light': '5B6B7F'},
       {'display': 'Cambria', 'body': 'Segoe UI', 'mono': 'Consolas'}),

    _t('Warm Charcoal', ['editorial', 'creative', 'media', 'design'],
       'Warm charcoal with an amber glow -- cinematic, refined, low-glare.', 'dark',
       {'bg_dark': '1C1A17', 'bg_light': 'FAF7F2', 'accent': 'F59E0B',
        'c1': '3A342B', 'c2': '6B5D45', 'c3': 'A08862', 'c4': 'D6BE90',
        'text_dark': 'F1ECE2', 'text_light': '1C1A17', 'dim_dark': 'B3A894', 'dim_light': '6E5D45'},
       {'display': 'Georgia', 'body': 'Corbel', 'mono': 'Consolas'}),

    _t('Deep Teal', ['medicine', 'science', 'research', 'marine'],
       'Deep teal-charcoal with a mint accent -- calm, clinical, comfortable in dark.', 'dark',
       {'bg_dark': '0C1E20', 'bg_light': 'F1FAFA', 'accent': '2DD4BF',
        'c1': '12363A', 'c2': '1E5A5E', 'c3': '3E8E90', 'c4': '7FC0C0',
        'text_dark': 'E6F3F2', 'text_light': '0C1E20', 'dim_dark': '8FB3B2', 'dim_light': '4E6669'},
       {'display': 'Cambria', 'body': 'Century Gothic', 'mono': 'Consolas'}),

    _t('Aubergine', ['arts', 'creative', 'philosophy', 'music'],
       'Deep plum-charcoal with a lilac accent -- moody, elegant, artful.', 'dark',
       {'bg_dark': '1A1220', 'bg_light': 'F8F5FB', 'accent': 'C084FC',
        'c1': '2E1F3D', 'c2': '4C2F63', 'c3': '7C5C99', 'c4': 'B49BCC',
        'text_dark': 'EFE9F4', 'text_light': '1A1220', 'dim_dark': 'B0A3BF', 'dim_light': '5F5878'},
       {'display': 'Book Antiqua', 'body': 'Candara', 'mono': 'Courier New'}),

    _t('Forest Night', ['environment', 'sustainability', 'biology', 'ecology'],
       'Deep forest-charcoal with a fresh mint-lime accent -- natural, focused, low-glare.', 'dark',
       {'bg_dark': '0E1A12', 'bg_light': 'F2F8F3', 'accent': '4ADE80',
        'c1': '14351F', 'c2': '256A3C', 'c3': '4E9E68', 'c4': '8FCBA1',
        'text_dark': 'E6F2E9', 'text_light': '0E1A12', 'dim_dark': '93B29C', 'dim_light': '56705E'},
       {'display': 'Cambria', 'body': 'Corbel', 'mono': 'Consolas'}),

    _t('Wine Cellar', ['luxury', 'arts', 'history', 'culinary'],
       'Deep wine-charcoal with a warm gold accent -- opulent, cinematic, refined.', 'dark',
       {'bg_dark': '1E1113', 'bg_light': 'FAF3F3', 'accent': 'D9A94E',
        'c1': '3A1E22', 'c2': '6E3038', 'c3': 'A85C5C', 'c4': 'D9A0A0',
        'text_dark': 'F1E3E4', 'text_light': '1E1113', 'dim_dark': 'BFA4A6', 'dim_light': '6E4E52'},
       {'display': 'Book Antiqua', 'body': 'Candara', 'mono': 'Courier New'}),

    _t('Slate Ink', ['technology', 'data-science', 'engineering', 'business'],
       'Cool slate-charcoal with a bright cyan accent -- crisp, technical, modern dark.', 'dark',
       {'bg_dark': '14181D', 'bg_light': 'F3F6F9', 'accent': '22D3EE',
        'c1': '1E2A33', 'c2': '33485A', 'c3': '5E7C92', 'c4': '97B2C4',
        'text_dark': 'E7EEF3', 'text_light': '14181D', 'dim_dark': '93A6B3', 'dim_light': '56656F'},
       {'display': 'Constantia', 'body': 'Segoe UI', 'mono': 'Consolas'}),

    _t('Copper Dusk', ['history', 'craft', 'anthropology', 'architecture'],
       'Warm brown-charcoal with a copper accent -- earthy, cinematic, characterful dark.', 'dark',
       {'bg_dark': '1C1512', 'bg_light': 'FAF5F0', 'accent': 'E08A5B',
        'c1': '3A241A', 'c2': '6E4530', 'c3': 'A87150', 'c4': 'DBAE8E',
        'text_dark': 'F2E7DD', 'text_light': '1C1512', 'dim_dark': 'BFA593', 'dim_light': '6E5545'},
       {'display': 'Georgia', 'body': 'Gill Sans MT', 'mono': 'Consolas'}),
]

# Per-theme heading treatment (rendered in deck_pptx_gen.js addTitle): each theme gets a distinctive
# title accent rather than plain bold. Styles: 'underline' (accent rule below), 'bar-left' (thick accent
# bar left of title), 'overline' (accent rule above), 'accent-lead' (leading token colored in accent).
_TITLE_STYLE: dict[str, str] = {
    'Azure Professional': 'bar-left',   'Terracotta Editorial': 'overline',
    'Forest Botanical': 'underline',    'Crimson Bold': 'bar-left',
    'Violet Creative': 'accent-lead',   'Slate Corporate': 'bar-left',
    'Amber Academic': 'overline',       'Rose Soft': 'underline',
    'Ocean Teal': 'bar-left',           'Indigo Data': 'accent-lead',
    'Sunset Coral': 'accent-lead',      'Emerald Fresh': 'underline',
    'Plum Wine': 'overline',            'Steel Minimal': 'bar-left',
    'Sky Clean': 'underline',           'Olive Muted': 'overline',
    'Midnight Navy': 'accent-lead',     'Warm Charcoal': 'overline',
    'Deep Teal': 'bar-left',            'Aubergine': 'accent-lead',
    # + 10 new (2026 research)
    'Burgundy Wine': 'bar-left',        'Sage Editorial': 'overline',
    'Dusty Rose': 'accent-lead',        'Lavender Mist': 'underline',
    'Turquoise Fresh': 'bar-left',      'Mustard Ochre': 'overline',
    'Forest Night': 'accent-lead',      'Wine Cellar': 'underline',
    'Slate Ink': 'bar-left',            'Copper Dusk': 'overline',
}
for _t_entry in THEME_LIBRARY:
    _t_entry['title_style'] = _TITLE_STYLE.get(_t_entry['name'], 'underline')

THEME_LIBRARY_BY_NAME: dict[str, dict] = {t['name']: t for t in THEME_LIBRARY}

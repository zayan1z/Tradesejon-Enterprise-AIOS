"""Full ~195-country search catalog and country-matched phone validation for Tradesejon Discord forms."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CountrySpec:
    iso2: str
    en: str
    ar: str
    dial: str
    min_nsn: int
    max_nsn: int
    example: str
    mobile_prefixes: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


def _norm_text(s: str) -> str:
    t = (s or "").strip().lower()
    t = re.sub(r"[أإآٱ]", "ا", t)
    t = t.replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي")
    t = re.sub(r"[\u064b-\u065f\u0670]", "", t)  # Arabic diacritics
    t = re.sub(r"[^\w\u0600-\u06ff\s]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Detailed rules for MENA + major global markets, followed by all remaining UN sovereign states.
_DETAILED_COUNTRIES: list[CountrySpec] = [
    CountrySpec("EG", "Egypt", "مصر", "20", 10, 10, "+201012345678", ("10", "11", "12", "15"), ("egypt", "masr", "misr", "مصر", "القاهره", "القاهرة")),
    CountrySpec("SA", "Saudi Arabia", "السعودية", "966", 9, 9, "+966501234567", ("5",), ("ksa", "saudi", "السعودية", "السعوديه", "المملكه")),
    CountrySpec("AE", "United Arab Emirates", "الإمارات", "971", 9, 9, "+971501234567", ("5",), ("uae", "emirates", "dubai", "الامارات", "الإمارات", "دبي")),
    CountrySpec("KW", "Kuwait", "الكويت", "965", 8, 8, "+96550123456", ("4", "5", "6", "9"), ("kuwait", "الكويت")),
    CountrySpec("QA", "Qatar", "قطر", "974", 8, 8, "+97450123456", ("3", "5", "6", "7"), ("qatar", "قطر")),
    CountrySpec("BH", "Bahrain", "البحرين", "973", 8, 8, "+97336123456", ("3", "6"), ("bahrain", "البحرين")),
    CountrySpec("OM", "Oman", "عُمان", "968", 8, 8, "+96891234567", ("7", "9"), ("oman", "عمان", "سلطنه عمان")),
    CountrySpec("JO", "Jordan", "الأردن", "962", 9, 9, "+962791234567", ("7",), ("jordan", "الاردن", "الأردن")),
    CountrySpec("IQ", "Iraq", "العراق", "964", 10, 10, "+9647701234567", ("7",), ("iraq", "العراق")),
    CountrySpec("LB", "Lebanon", "لبنان", "961", 7, 8, "+96170123456", ("3", "7", "8"), ("lebanon", "لبنان")),
    CountrySpec("PS", "Palestine", "فلسطين", "970", 9, 9, "+970591234567", ("5",), ("palestine", "فلسطين")),
    CountrySpec("SY", "Syria", "سوريا", "963", 9, 9, "+963931234567", ("9",), ("syria", "سوريا")),
    CountrySpec("YE", "Yemen", "اليمن", "967", 9, 9, "+967771234567", ("7",), ("yemen", "اليمن")),
    CountrySpec("SD", "Sudan", "السودان", "249", 9, 9, "+249912345678", ("9", "1"), ("sudan", "السودان")),
    CountrySpec("LY", "Libya", "ليبيا", "218", 9, 9, "+218912345678", ("9",), ("libya", "ليبيا")),
    CountrySpec("TN", "Tunisia", "تونس", "216", 8, 8, "+21620123456", ("2", "4", "5", "9"), ("tunisia", "تونس")),
    CountrySpec("DZ", "Algeria", "الجزائر", "213", 9, 9, "+213551234567", ("5", "6", "7"), ("algeria", "الجزائر")),
    CountrySpec("MA", "Morocco", "المغرب", "212", 9, 9, "+212612345678", ("5", "6", "7"), ("morocco", "المغرب")),
    CountrySpec("MR", "Mauritania", "موريتانيا", "222", 8, 8, "+22222123456", (), ("mauritania", "موريتانيا")),
    CountrySpec("SO", "Somalia", "الصومال", "252", 8, 9, "+252612345678", (), ("somalia", "الصومال")),
    CountrySpec("DJ", "Djibouti", "جيبوتي", "253", 8, 8, "+25377123456", (), ("djibouti", "جيبوتي")),
    CountrySpec("KM", "Comoros", "جزر القمر", "269", 7, 7, "+2693212345", (), ("comoros", "جزر القمر")),
    CountrySpec("US", "United States", "أمريكا", "1", 10, 10, "+12025550123", (), ("usa", "us", "america", "united states", "امريكا", "الولايات المتحده")),
    CountrySpec("CA", "Canada", "كندا", "1", 10, 10, "+14165550123", (), ("canada", "كندا")),
    CountrySpec("GB", "United Kingdom", "بريطانيا", "44", 10, 10, "+447700900123", ("7",), ("uk", "britain", "england", "united kingdom", "بريطانيا", "انجلترا", "المملكه المتحده")),
    CountrySpec("DE", "Germany", "ألمانيا", "49", 10, 11, "+4915123456789", ("1",), ("germany", "deutschland", "المانيا")),
    CountrySpec("FR", "France", "فرنسا", "33", 9, 9, "+33612345678", ("6", "7"), ("france", "فرنسا")),
    CountrySpec("TR", "Turkey", "تركيا", "90", 10, 10, "+905321234567", ("5",), ("turkey", "turkiye", "تركيا")),
    CountrySpec("IT", "Italy", "إيطاليا", "39", 9, 10, "+393471234567", ("3",), ("italy", "ايطاليا")),
    CountrySpec("ES", "Spain", "إسبانيا", "34", 9, 9, "+34612345678", ("6", "7"), ("spain", "اسبانيا")),
    CountrySpec("NL", "Netherlands", "هولندا", "31", 9, 9, "+31612345678", ("6",), ("netherlands", "holland", "هولندا")),
    CountrySpec("AU", "Australia", "أستراليا", "61", 9, 9, "+61412345678", ("4",), ("australia", "استراليا")),
    CountrySpec("IN", "India", "الهند", "91", 10, 10, "+919812345678", ("6", "7", "8", "9"), ("india", "الهند")),
    CountrySpec("PK", "Pakistan", "باكستان", "92", 10, 10, "+923001234567", ("3",), ("pakistan", "باكستان")),
    CountrySpec("MY", "Malaysia", "ماليزيا", "60", 9, 10, "+60123456789", ("1",), ("malaysia", "ماليزيا")),
    CountrySpec("ID", "Indonesia", "إندونيسيا", "62", 9, 12, "+628123456789", ("8",), ("indonesia", "اندونيسيا")),
]

# Remaining ~160 sovereign countries (iso2, en, ar, dial, min_nsn, max_nsn)
_EXTRA_COUNTRIES_RAW: tuple[tuple[str, str, str, str, int, int], ...] = (
    ("AF", "Afghanistan", "أفغانستان", "93", 9, 9),
    ("AL", "Albania", "ألبانيا", "355", 9, 9),
    ("AD", "Andorra", "أندورا", "376", 6, 9),
    ("AO", "Angola", "أنغولا", "244", 9, 9),
    ("AG", "Antigua and Barbuda", "أنتيغوا وبربودا", "1", 10, 10),
    ("AR", "Argentina", "الأرجنتين", "54", 10, 11),
    ("AM", "Armenia", "أرمينيا", "374", 8, 8),
    ("AT", "Austria", "النمسا", "43", 10, 11),
    ("AZ", "Azerbaijan", "أذربيجان", "994", 9, 9),
    ("BS", "Bahamas", "الباهاما", "1", 10, 10),
    ("BD", "Bangladesh", "بنغلاديش", "880", 10, 10),
    ("BB", "Barbados", "بربادوس", "1", 10, 10),
    ("BY", "Belarus", "بيلاروسيا", "375", 9, 9),
    ("BE", "Belgium", "بلجيكا", "32", 9, 9),
    ("BZ", "Belize", "بليز", "501", 7, 7),
    ("BJ", "Benin", "بنين", "229", 8, 10),
    ("BT", "Bhutan", "بوتان", "975", 8, 8),
    ("BO", "Bolivia", "بوليفيا", "591", 8, 8),
    ("BA", "Bosnia and Herzegovina", "البوسنة والهرسك", "387", 8, 9),
    ("BW", "Botswana", "بوتسوانا", "267", 8, 8),
    ("BR", "Brazil", "البرازيل", "55", 10, 11),
    ("BN", "Brunei", "بروناي", "673", 7, 7),
    ("BG", "Bulgaria", "بلغاريا", "359", 8, 9),
    ("BF", "Burkina Faso", "بوركينا فاسو", "226", 8, 8),
    ("BI", "Burundi", "بوروندي", "257", 8, 8),
    ("CV", "Cabo Verde", "الرأس الأخضر", "238", 7, 7),
    ("KH", "Cambodia", "كمبوديا", "855", 8, 9),
    ("CM", "Cameroon", "الكاميرون", "237", 9, 9),
    ("CF", "Central African Republic", "أفريقيا الوسطى", "236", 8, 8),
    ("TD", "Chad", "تشاد", "235", 8, 8),
    ("CL", "Chile", "تشيلي", "56", 9, 9),
    ("CN", "China", "الصين", "86", 11, 11),
    ("CO", "Colombia", "كولومبيا", "57", 10, 10),
    ("CG", "Congo", "الكونغو", "242", 9, 9),
    ("CD", "DR Congo", "الكونغو الديمقراطية", "243", 9, 9),
    ("CR", "Costa Rica", "كوستاريكا", "506", 8, 8),
    ("HR", "Croatia", "كرواتيا", "385", 8, 9),
    ("CU", "Cuba", "كوبا", "53", 8, 8),
    ("CY", "Cyprus", "قبرص", "357", 8, 8),
    ("CZ", "Czechia", "التشيك", "420", 9, 9),
    ("DK", "Denmark", "الدنمارك", "45", 8, 8),
    ("DM", "Dominica", "دومينيكا", "1", 10, 10),
    ("DO", "Dominican Republic", "الدومينيكان", "1", 10, 10),
    ("EC", "Ecuador", "الإكوادور", "593", 9, 9),
    ("SV", "El Salvador", "السلفادور", "503", 8, 8),
    ("GQ", "Equatorial Guinea", "غينيا الاستوائية", "240", 9, 9),
    ("ER", "Eritrea", "إريتريا", "291", 7, 7),
    ("EE", "Estonia", "إستونيا", "372", 7, 8),
    ("SZ", "Eswatini", "إسواتيني", "268", 8, 8),
    ("ET", "Ethiopia", "إثيوبيا", "251", 9, 9),
    ("FJ", "Fiji", "فيجي", "679", 7, 7),
    ("FI", "Finland", "فنلندا", "358", 8, 10),
    ("GA", "Gabon", "الغابون", "241", 7, 8),
    ("GM", "Gambia", "غامبيا", "220", 7, 7),
    ("GE", "Georgia", "جورجيا", "995", 9, 9),
    ("GH", "Ghana", "غانا", "233", 9, 9),
    ("GR", "Greece", "اليونان", "30", 10, 10),
    ("GD", "Grenada", "غرينادا", "1", 10, 10),
    ("GT", "Guatemala", "غواتيمالا", "502", 8, 8),
    ("GN", "Guinea", "غينيا", "224", 9, 9),
    ("GW", "Guinea-Bissau", "غينيا بيساو", "245", 7, 9),
    ("GY", "Guyana", "غيانا", "592", 7, 7),
    ("HT", "Haiti", "هايتي", "509", 8, 8),
    ("HN", "Honduras", "هندوراس", "504", 8, 8),
    ("HU", "Hungary", "المجر", "36", 9, 9),
    ("IS", "Iceland", "آيسلندا", "354", 7, 7),
    ("IR", "Iran", "إيران", "98", 10, 10),
    ("IE", "Ireland", "أيرلندا", "353", 9, 9),
    ("IL", "Israel", "إسرائيل", "972", 9, 9),
    ("CI", "Ivory Coast", "ساحل العاج", "225", 10, 10),
    ("JM", "Jamaica", "جامايكا", "1", 10, 10),
    ("JP", "Japan", "اليابان", "81", 10, 10),
    ("KZ", "Kazakhstan", "كازاخستان", "7", 10, 10),
    ("KE", "Kenya", "كينيا", "254", 9, 9),
    ("KI", "Kiribati", "كيريباتي", "686", 5, 8),
    ("KP", "North Korea", "كوريا الشمالية", "850", 8, 10),
    ("KR", "South Korea", "كوريا الجنوبية", "82", 9, 10),
    ("KG", "Kyrgyzstan", "قيرغيزستان", "996", 9, 9),
    ("LA", "Laos", "لاوس", "856", 8, 10),
    ("LV", "Latvia", "لاتفيا", "371", 8, 8),
    ("LS", "Lesotho", "ليسوتو", "266", 8, 8),
    ("LR", "Liberia", "ليبيريا", "231", 7, 9),
    ("LI", "Liechtenstein", "ليختنشتاين", "423", 7, 9),
    ("LT", "Lithuania", "ليتوانيا", "370", 8, 8),
    ("LU", "Luxembourg", "لوكسمبورغ", "352", 8, 9),
    ("MG", "Madagascar", "مدغشقر", "261", 9, 9),
    ("MW", "Malawi", "مالاوي", "265", 9, 9),
    ("MV", "Maldives", "المالديف", "960", 7, 7),
    ("ML", "Mali", "مالي", "223", 8, 8),
    ("MT", "Malta", "مالطا", "356", 8, 8),
    ("MH", "Marshall Islands", "جزر مارشال", "692", 7, 7),
    ("MU", "Mauritius", "موريشيوس", "230", 8, 8),
    ("MX", "Mexico", "المكسيك", "52", 10, 10),
    ("FM", "Micronesia", "ميكرونيسيا", "691", 7, 7),
    ("MD", "Moldova", "مولدوفا", "373", 8, 8),
    ("MC", "Monaco", "موناكو", "377", 8, 9),
    ("MN", "Mongolia", "منغوليا", "976", 8, 8),
    ("ME", "Montenegro", "الجبل الأسود", "382", 8, 8),
    ("MZ", "Mozambique", "موزمبيق", "258", 9, 9),
    ("MM", "Myanmar", "ميانمار", "95", 8, 10),
    ("NA", "Namibia", "ناميبيا", "264", 9, 9),
    ("NR", "Nauru", "ناورو", "674", 7, 7),
    ("NP", "Nepal", "نيبال", "977", 10, 10),
    ("NZ", "New Zealand", "نيوزيلندا", "64", 8, 10),
    ("NI", "Nicaragua", "نيكاراغوا", "505", 8, 8),
    ("NE", "Niger", "النيجر", "227", 8, 8),
    ("NG", "Nigeria", "نيجيريا", "234", 10, 10),
    ("MK", "North Macedonia", "مقدونيا الشمالية", "389", 8, 8),
    ("NO", "Norway", "النرويج", "47", 8, 8),
    ("PW", "Palau", "بالاو", "680", 7, 7),
    ("PA", "Panama", "بنما", "507", 8, 8),
    ("PG", "Papua New Guinea", "بابوا غينيا الجديدة", "675", 8, 8),
    ("PY", "Paraguay", "باراغواي", "595", 9, 9),
    ("PE", "Peru", "بيرو", "51", 9, 9),
    ("PH", "Philippines", "الفلبين", "63", 10, 10),
    ("PL", "Poland", "بولندا", "48", 9, 9),
    ("PT", "Portugal", "البرتغال", "351", 9, 9),
    ("RO", "Romania", "رومانيا", "40", 9, 9),
    ("RU", "Russia", "روسيا", "7", 10, 10),
    ("RW", "Rwanda", "رواندا", "250", 9, 9),
    ("KN", "Saint Kitts and Nevis", "سانت كيتس ونيفيس", "1", 10, 10),
    ("LC", "Saint Lucia", "سانت لوسيا", "1", 10, 10),
    ("VC", "Saint Vincent", "سانت فنسنت", "1", 10, 10),
    ("WS", "Samoa", "ساموا", "685", 7, 7),
    ("SM", "San Marino", "سان مارينو", "378", 8, 10),
    ("ST", "Sao Tome and Principe", "ساو تومي وبرينسيبي", "239", 7, 7),
    ("SN", "Senegal", "السنغال", "221", 9, 9),
    ("RS", "Serbia", "صربيا", "381", 8, 9),
    ("SC", "Seychelles", "سيشل", "248", 7, 7),
    ("SL", "Sierra Leone", "سيراليون", "232", 8, 8),
    ("SG", "Singapore", "سنغافورة", "65", 8, 8),
    ("SK", "Slovakia", "سلوفاكيا", "421", 9, 9),
    ("SI", "Slovenia", "سلوفينيا", "386", 8, 8),
    ("SB", "Solomon Islands", "جزر سليمان", "677", 7, 7),
    ("ZA", "South Africa", "جنوب أفريقيا", "27", 9, 9),
    ("SS", "South Sudan", "جنوب السودان", "211", 9, 9),
    ("LK", "Sri Lanka", "سريلانكا", "94", 9, 9),
    ("SR", "Suriname", "سورينام", "597", 7, 7),
    ("SE", "Sweden", "السويد", "46", 9, 9),
    ("CH", "Switzerland", "سويسرا", "41", 9, 9),
    ("TW", "Taiwan", "تايوان", "886", 9, 9),
    ("TJ", "Tajikistan", "طاجيكستان", "992", 9, 9),
    ("TZ", "Tanzania", "تنزانيا", "255", 9, 9),
    ("TH", "Thailand", "تايلاند", "66", 9, 9),
    ("TL", "Timor-Leste", "تيمور الشرقية", "670", 7, 8),
    ("TG", "Togo", "توغو", "228", 8, 8),
    ("TO", "Tonga", "تونغا", "676", 7, 7),
    ("TT", "Trinidad and Tobago", "ترينيداد وتوباغو", "1", 10, 10),
    ("TM", "Turkmenistan", "تركمانستان", "993", 8, 8),
    ("TV", "Tuvalu", "توفالو", "688", 5, 7),
    ("UG", "Uganda", "أوغندا", "256", 9, 9),
    ("UA", "Ukraine", "أوكرانيا", "380", 9, 9),
    ("UY", "Uruguay", "أوروغواي", "598", 8, 8),
    ("UZ", "Uzbekistan", "أوزبكستان", "998", 9, 9),
    ("VU", "Vanuatu", "فانواتو", "678", 7, 7),
    ("VA", "Vatican City", "الفاتيكان", "379", 8, 10),
    ("VE", "Venezuela", "فنزويلا", "58", 10, 10),
    ("VN", "Vietnam", "فيتنام", "84", 9, 10),
    ("ZM", "Zambia", "زامبيا", "260", 9, 9),
    ("ZW", "Zimbabwe", "زيمبابوي", "263", 9, 9),
)

ALL_COUNTRIES: tuple[CountrySpec, ...] = tuple(
    _DETAILED_COUNTRIES
    + [
        CountrySpec(
            iso2=iso2,
            en=en,
            ar=ar,
            dial=dial,
            min_nsn=mn,
            max_nsn=mx,
            example=f"+{dial}{'5' * mn}",
            aliases=(en.lower(), ar),
        )
        for iso2, en, ar, dial, mn, mx in _EXTRA_COUNTRIES_RAW
    ]
)

_EXACT_INDEX: dict[str, CountrySpec] = {}
for _c in ALL_COUNTRIES:
    for _key in (_c.iso2, _c.en, _c.ar, *_c.aliases):
        _nk = _norm_text(_key)
        if _nk and _nk not in _EXACT_INDEX:
            _EXACT_INDEX[_nk] = _c


def resolve_country_from_text(raw: str) -> CountrySpec | None:
    """Resolve any world country by English name, Arabic name, ISO-2 code, or prefix."""
    q = _norm_text(raw)
    if not q or len(q) < 2 or q.isdigit():
        return None
    if q in _EXACT_INDEX:
        return _EXACT_INDEX[q]
    # Prefix match on normalized keys
    for key, spec in _EXACT_INDEX.items():
        if len(key) >= 3 and (key.startswith(q) or (len(q) >= 4 and q in key)):
            return spec
    return None


def _is_fake_digits(digits: str) -> bool:
    if len(set(digits)) <= 2:
        return True
    if digits in ("0123456789", "1234567890", "9876543210", "12345678"):
        return True
    return False


def validate_phone_for_country(raw_phone: str, raw_country: str = "") -> tuple[bool, str, str]:
    """Validate phone strictly against the chosen country's dial code, length, and mobile prefix."""
    raw = (raw_phone or "").strip()
    country_spec = resolve_country_from_text(raw_country) if raw_country else None
    ex = country_spec.example if country_spec else "+201012345678"

    if not raw:
        return False, f"رقم الهاتف / الموبايل (Number) مطلوب — اكتب رقمك الصحيح مثل {ex}.", ""

    # Reject letters or unexpected characters
    cleaned = re.sub(r"[\s\-\(\)\.]", "", raw)
    if not re.match(r"^\+?\d+$", cleaned):
        return False, f"رقم الهاتف / الموبايل (Number) غير صحيح: يحتوي على حروف أو رموز غير مسموحة — اكتب أرقاماً فقط مثل {ex}.", ""

    has_plus = cleaned.startswith("+")
    digits_only = cleaned[1:] if has_plus else cleaned
    if digits_only.startswith("00"):
        has_plus = True
        digits_only = digits_only[2:]

    if _is_fake_digits(digits_only):
        return False, f"رقم الهاتف / الموبايل (Number) غير حقيقي (أرقام مكررة أو وهمية) — اكتب رقمك الحقيقي مثل {ex}.", ""

    if country_spec is not None:
        dial = country_spec.dial
        # Determine national significant number (NSN)
        if has_plus:
            if not digits_only.startswith(dial):
                return (
                    False,
                    f"رقم الموبايل (Number) لا يطابق البلد المختار ({country_spec.ar} / {country_spec.en} +{dial}) — اكتب رقم يبدأ بـ +{dial} مثل {country_spec.example}.",
                    "",
                )
            nsn = digits_only[len(dial):]
        elif digits_only.startswith(dial) and len(digits_only) >= len(dial) + country_spec.min_nsn:
            nsn = digits_only[len(dial):]
        else:
            nsn = digits_only.lstrip("0")

        if len(nsn) < country_spec.min_nsn:
            return (
                False,
                f"رقم الموبايل لـ {country_spec.ar} (+{dial}) قصير جداً ({len(nsn)} أرقام بدلاً من {country_spec.min_nsn}) — الصيغة الصحيحة: {country_spec.example}.",
                "",
            )
        if len(nsn) > country_spec.max_nsn:
            return (
                False,
                f"رقم الموبايل لـ {country_spec.ar} (+{dial}) طويل جداً ({len(nsn)} أرقام والحد الأقصى {country_spec.max_nsn}) — الصيغة الصحيحة: {country_spec.example}.",
                "",
            )
        if country_spec.mobile_prefixes and not nsn.startswith(country_spec.mobile_prefixes):
            if country_spec.iso2 == "EG":
                return (
                    False,
                    "رقم الموبايل المصري غير صحيح: يجب أن يبدأ بـ 010 أو 011 أو 012 أو 015 (مثال: +201012345678 أو 01012345678).",
                    "",
                )
            prefixes_str = " / ".join(country_spec.mobile_prefixes)
            return (
                False,
                f"رقم الموبايل لـ {country_spec.ar} (+{dial}) غير صحيح: يجب أن يبدأ بـ ({prefixes_str}) بعد +{dial} (مثال: {country_spec.example}).",
                "",
            )
        return True, "ok", f"+{dial}{nsn}"

    # Fallback when no country is specified: Egypt local/intl or valid E.164 (8-15 digits)
    if digits_only.startswith("20") and len(digits_only) == 12 and digits_only[2:4] in ("10", "11", "12", "15"):
        return True, "ok", f"+{digits_only}"
    if digits_only.startswith("01") and len(digits_only) == 11 and digits_only[1:3] in ("10", "11", "12", "15"):
        return True, "ok", f"+20{digits_only[1:]}"
    if digits_only.startswith("1") and len(digits_only) == 10 and digits_only[:2] in ("10", "11", "12", "15"):
        return True, "ok", f"+20{digits_only}"
    if len(digits_only) < 8:
        return False, "رقم الهاتف / الموبايل (Number) قصير جداً — أدخل رقم مصري صحيح مثل +201012345678 أو رقم دولي كامل مع كود الدولة.", ""
    if len(digits_only) > 15:
        return False, "رقم الهاتف / الموبايل (Number) طويل جداً (الحد الأقصى 15 رقماً دولياً) — الصيغة الصحيحة: +201012345678.", ""
    if has_plus and 8 <= len(digits_only) <= 15:
        return True, "ok", f"+{digits_only}"
    return False, "رقم الهاتف / الموبايل (Number) غير صحيح — اكتب كود الدولة قبل الرقم مثل +201012345678 أو +966501234567.", ""

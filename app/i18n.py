"""Internationalization labels, consent messages, and safety actions."""

from __future__ import annotations

SUPPORTED_REPORT_LANGUAGES: dict[str, str] = {
    "auto": "Auto (match audio language)",
    "en": "English",
    "hi": "हिन्दी (Hindi)",
    "kn": "ಕನ್ನಡ (Kannada)",
    "ta": "தமிழ் (Tamil)",
    "te": "తెలుగు (Telugu)",
}

_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "report_title": "VOICEGUARD AI: SECURITY REPORT",
        "voice_auth_header": "VOICE AUTHENTICITY (Aletheia)",
        "comm_risk_header": "COMMUNICATION RISK (Gemini)",
        "indicators_header": "INDICATORS",
        "assessment_header": "ASSESSMENT",
        "actions_header": "WHAT TO DO",
        "disclaimer": (
            "Automated assessment, not proof of fraud or voice cloning. "
            "Verify independently."
        ),
        "evidence_note": "A detection score is evidence, not proof.",
        "acknowledgement": "Analysing your audio with Aletheia + Gemini (usually under 30 s)...",
        "unsupported_type": (
            "Please forward or record a voice note or audio file (minimum 4 seconds). "
            "Images, videos, stickers, and documents are not supported for voice screening."
        ),
        "welcome": (
            "🛡️ Welcome to VoiceGuard AI.\n\n"
            "Forward any suspicious voice note or call recording (at least 4 seconds) "
            "and I will check both:\n"
            "• Voice authenticity via DetectifAI Aletheia\n"
            "• Scam & manipulation tactics via Gemini\n\n"
            "🔒 Privacy & Consent Notice: When you send audio, it is transmitted to "
            "DetectifAI Aletheia and Google Gemini solely for real-time analysis. "
            "VoiceGuard AI does not store your audio, transcripts, or phone number.\n\n"
            "Commands:\n"
            "• Send or forward an audio/voice message to scan\n"
            "• Reply 'lang en', 'lang hi', 'lang kn', 'lang ta', or 'lang te' to switch report language"
        ),
        "lang_updated": "Report language set to: {language}.",
        "lang_invalid": "Unsupported language code. Try: en, hi, kn, ta, te, or auto.",
        "rate_limited": "Rate limit reached. Please wait a bit before sending another recording.",
        "no_recent_report": "No recent scan found in memory. Send a voice note or audio file to start a new scan.",
        "how_to_report_in": (
            "🚨 How to report cyber fraud in India:\n"
            "• Call the National Cyber Crime Helpline: 1930 (24x7) immediately if money was sent or requested.\n"
            "• File an online complaint at https://cybercrime.gov.in\n"
            "• Keep call logs, UPI transaction IDs, screenshots, and the caller's number.\n"
            "• Family Safe-Word Tip: Agree on a private family code word offline so anyone calling in an 'emergency' can be verified instantly."
        ),
        "how_to_report_generic": (
            "🚨 How to report:\n"
            "• Contact your local cybercrime authority and your bank's official fraud helpline immediately.\n"
            "• Preserve call recordings, caller numbers, and transaction references.\n"
            "• Family Safe-Word Tip: Agree on a private family code word offline so urgent callers claiming to be relatives can be verified."
        ),
    },
    "hi": {
        "report_title": "VOICEGUARD AI: सुरक्षा रिपोर्ट",
        "voice_auth_header": "आवाज़ की प्रामाणिकता (Aletheia)",
        "comm_risk_header": "संदेश जोखिम (Gemini)",
        "indicators_header": "मुख्य संकेत",
        "assessment_header": "निष्कर्ष",
        "actions_header": "क्या करें",
        "disclaimer": (
            "यह स्वचालित जांच है, धोखाधड़ी या वॉइस क्लोनिंग का अंतिम प्रमाण नहीं। "
            "कृपया स्वतंत्र रूप से सत्यापित करें।"
        ),
        "evidence_note": "डिटेक्शन स्कोर केवल संकेत/साक्ष्य है, अंतिम प्रमाण नहीं।",
        "acknowledgement": "आपके ऑडियो की Aletheia और Gemini से जांच हो रही है (आमतौर पर 30 सेकंड से कम)...",
        "unsupported_type": (
            "कृपया कम से कम 4 सेकंड का वॉइस नोट या ऑडियो फ़ाइल भेजें। "
            "फ़ोटो, वीडियो, स्टिकर या दस्तावेज़ समर्थित नहीं हैं।"
        ),
        "welcome": (
            "🛡️ VoiceGuard AI में आपका स्वागत है।\n\n"
            "कोई भी संदिग्ध वॉइस नोट या कॉल रिकॉर्डिंग (कम से कम 4 सेकंड) फ़ॉरवर्ड करें।\n"
            "🔒 गोपनीयता सूचना: आपका ऑडियो केवल जांच के लिए DetectifAI Aletheia और Google Gemini को भेजा जाता है। "
            "VoiceGuard AI कोई ऑडियो, ट्रांसक्रिप्ट या फोन नंबर सेव नहीं करता।\n\n"
            "भाषा बदलने के लिए भेजें: lang en, lang hi, lang kn, lang ta, या lang te"
        ),
        "lang_updated": "रिपोर्ट भाषा सेट की गई: {language}.",
        "lang_invalid": "अमान्य भाषा कोड। उपयोग करें: en, hi, kn, ta, te, या auto.",
        "rate_limited": "सीमा समाप्त हो गई है। कृपया थोड़ी देर बाद फिर प्रयास करें।",
        "no_recent_report": "मेमोरी में कोई हालिया रिपोर्ट नहीं मिली। नई जांच के लिए ऑडियो भेजें।",
        "how_to_report_in": (
            "🚨 भारत में साइबर धोखाधड़ी की रिपोर्ट कैसे करें:\n"
            "• तुरंत राष्ट्रीय साइबर क्राइम हेल्पलाइन 1930 (24x7) पर कॉल करें।\n"
            "• https://cybercrime.gov.in पर ऑनलाइन शिकायत दर्ज करें।\n"
            "• कॉल लॉग, UPI रेफरेंस और स्क्रीनशॉट सुरक्षित रखें।\n"
            "• फैमिली सेफ-वर्ड टिप: परिवार के साथ एक गुप्त कोड वर्ड तय करें ताकि इमरजेंसी कॉल की पुष्टि की जा सके।"
        ),
        "how_to_report_generic": (
            "🚨 रिपोर्ट कैसे करें:\n"
            "• तुरंत अपने बैंक और स्थानीय साइबर क्राइम पुलिस से संपर्क करें।\n"
            "• फैमिली सेफ-वर्ड टिप: परिवार के साथ एक गुप्त कोड वर्ड तय करें।"
        ),
    },
    "kn": {
        "report_title": "VOICEGUARD AI: ಭದ್ರತಾ ವರದಿ",
        "voice_auth_header": "ಧ್ವನಿಯ ಸತ್ಯಾಸತ್ಯತೆ (Aletheia)",
        "comm_risk_header": "ಸಂವಹನ ಅಪಾಯ (Gemini)",
        "indicators_header": "ಸೂಚಕಗಳು",
        "assessment_header": "ಮೌಲ್ಯಮಾಪನ",
        "actions_header": "ಮುಂದೇನು ಮಾಡಬೇಕು",
        "disclaimer": (
            "ಇದು ಸ್ವಯಂಚಾಲಿತ ಮೌಲ್ಯಮಾಪನವಾಗಿದೆ, ವಂಚನೆ ಅಥವಾ ಧ್ವನಿ ಕ್ಲೋನಿಂಗ್‌ನ ಅಂತಿಮ ಪುರಾವೆಯಲ್ಲ. "
            "ದಯವಿಟ್ಟು ಸ್ವತಂತ್ರವಾಗಿ ಪರಿಶೀಲಿಸಿ."
        ),
        "evidence_note": "ಪತ್ತೆ ಸ್ಕೋರ್ ಕೇವಲ ಸಾಕ್ಷ್ಯ/ಸೂಚನೆ, ಅಂತಿಮ ಪುರಾವೆಯಲ್ಲ.",
        "acknowledgement": "ನಿಮ್ಮ ಆಡಿಯೋವನ್ನು Aletheia ಮತ್ತು Gemini ಮೂಲಕ ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ (ಸಾಮಾನ್ಯವಾಗಿ 30 ಸೆಕೆಂಡುಗಳಿಗಿಂತ ಕಡಿಮೆ)...",
        "unsupported_type": (
            "ದಯವಿಟ್ಟು ಕನಿಷ್ಠ 4 ಸೆಕೆಂಡುಗಳ ಧ್ವನಿ ಸಂದೇಶ ಅಥವಾ ಆಡಿಯೋ ಫೈಲ್ ಕಳುಹಿಸಿ. "
            "ಚಿತ್ರಗಳು, ವೀಡಿಯೊಗಳು ಮತ್ತು ದಾಖಲೆಗಳು ಬೆಂಬಲಿತವಾಗಿಲ್ಲ."
        ),
        "welcome": (
            "🛡️ VoiceGuard AI ಗೆ ಸ್ವಾಗತ.\n\n"
            "ಯಾವುದೇ ಅನುಮಾನಾಸ್ಪದ ಧ್ವನಿ ಸಂದೇಶ ಅಥವಾ ಕರೆ ರೆಕಾರ್ಡಿಂಗ್ (ಕನಿಷ್ಠ 4 ಸೆಕೆಂಡುಗಳು) ಫಾರ್ವರ್ಡ್ ಮಾಡಿ.\n"
            "🔒 ಗೌಪ್ಯತೆ ಸೂಚನೆ: ಪರಿಶೀಲನೆಗಾಗಿ ಮಾತ್ರ ಆಡಿಯೋವನ್ನು DetectifAI Aletheia ಮತ್ತು Google Gemini ಗೆ ಕಳುಹಿಸಲಾಗುತ್ತದೆ. "
            "VoiceGuard AI ಆಡಿಯೋ ಅಥವಾ ಫೋನ್ ಸಂಖ್ಯೆಯನ್ನು ಸಂಗ್ರಹಿಸುವುದಿಲ್ಲ.\n\n"
            "ಭಾಷೆ ಬದಲಾಯಿಸಲು: lang en, lang hi, lang kn, lang ta, ಅಥವಾ lang te"
        ),
        "lang_updated": "ವರದಿ ಭಾಷೆಯನ್ನು ಬದಲಾಯಿಸಲಾಗಿದೆ: {language}.",
        "lang_invalid": "ಅಮಾನ್ಯ ಭಾಷಾ ಕೋಡ್. ಪ್ರಯತ್ನಿಸಿ: en, hi, kn, ta, te, ಅಥವಾ auto.",
        "rate_limited": "ಮಿತಿ ತಲುಪಿದೆ. ದಯವಿಟ್ಟು ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
        "no_recent_report": "ಇತ್ತೀಚಿನ ವರದಿ ಕಂಡುಬಂದಿಲ್ಲ. ಹೊಸ ಪರಿಶೀಲನೆಗಾಗಿ ಆಡಿಯೋ ಕಳುಹಿಸಿ.",
        "how_to_report_in": (
            "🚨 ಭಾರತದಲ್ಲಿ ಸೈಬರ್ ವಂಚನೆ ದೂರು ನೀಡುವುದು ಹೇಗೆ:\n"
            "• ತಕ್ಷಣ ರಾಷ್ಟ್ರೀಯ ಸೈಬರ್ ಕ್ರೈಮ್ ಸಹಾಯವಾಣಿ 1930 (24x7) ಗೆ ಕರೆ ಮಾಡಿ.\n"
            "• https://cybercrime.gov.in ನಲ್ಲಿ ದೂರು ದಾಖಲಿಸಿ.\n"
            "• ಕರೆ ವಿವರಗಳು ಮತ್ತು UPI ವಹಿವಾಟಿನ ಮಾಹಿತಿಯನ್ನು ಉಳಿಸಿಕೊಳ್ಳಿ.\n"
            "• ಫ್ಯಾಮಿಲಿ ಸೇಫ್-ವರ್ಡ್: ತುರ್ತು ಕರೆಗಳನ್ನು ಪರಿಶೀಲಿಸಲು ಕುಟುಂಬದವರೊಂದಿಗೆ ಗುಪ್ತ ಕೋಡ್ ಪದವನ್ನು ನಿಗದಿಪಡಿಸಿ."
        ),
        "how_to_report_generic": (
            "🚨 ದೂರು ನೀಡುವುದು ಹೇಗೆ:\n"
            "• ತಕ್ಷಣ ನಿಮ್ಮ ಬ್ಯಾಂಕ್ ಮತ್ತು ಸ್ಥಳೀಯ ಸೈಬರ್ ಅಪರಾಧ ಘಟಕವನ್ನು ಸಂಪರ್ಕಿಸಿ.\n"
            "• ಫ್ಯಾಮಿಲಿ ಸೇಫ್-ವರ್ಡ್: ಕುಟುಂಬದ ತುರ್ತು ಕರೆಗಳನ್ನು ಪರಿಶೀಲಿಸಲು ಗುಪ್ತ ಪದವನ್ನು ಬಳಸಿ."
        ),
    },
}


def normalize_language(lang: str | None, default: str = "en") -> str:
    """Normalize user language code; defaults safely to supported codes."""
    if not lang:
        return default if default in SUPPORTED_REPORT_LANGUAGES else "en"
    cleaned = lang.strip().lower()
    if cleaned in SUPPORTED_REPORT_LANGUAGES:
        return cleaned
    return default if default in SUPPORTED_REPORT_LANGUAGES else "en"


def get_label(language: str, key: str) -> str:
    """Return localized static UI/WhatsApp string with fallback to English."""
    lang = normalize_language(language)
    table = _LABELS.get(lang) or _LABELS["en"]
    return table.get(key) or _LABELS["en"].get(key, key)


def language_display_name(language: str) -> str:
    lang = normalize_language(language)
    return SUPPORTED_REPORT_LANGUAGES.get(lang, "English")

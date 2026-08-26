"""Internationalization for JARVIS UI."""
import json
import os

TRANSLATIONS = {
    "en": {
        "setup_title": "J.A.R.V.I.S SETUP",
        "terms_heading": "1. Terms of Use",
        "terms_agree": "I have read and agree to the Terms of Use",
        "continue": "CONTINUE",
        "api_key_heading": "2. Your Zen API key",
        "api_key_hint": "Create one free at opencode.ai/auth, then paste it here.",
        "validate_key": "VALIDATE KEY",
        "validating": "Validating...",
        "key_valid": "Key valid - {count} models available.",
        "system_check_heading": "3. System check",
        "system_check_hint": "Making sure your hardware is ready...",
        "mic": "Microphone",
        "speakers": "Speakers",
        "internet": "Internet",
        "voice_engine": "Voice engine",
        "brain_heading": "4. Pick JARVIS's brain",
        "brain_hint": "Free models are listed first. You can change this anytime in the top bar.",
        "save_continue": "SAVE & CONTINUE",
        "voice_heading": "5. Voice lock (optional)",
        "voice_hint": "Enroll your voice so only you can command JARVIS by wake word.",
        "enroll_voice": "ENROLL MY VOICE",
        "skip": "SKIP FOR NOW",
        "all_set": "All set, sir.",
        "start": "START JARVIS",
        "wake_off": "WAKE: OFF",
        "wake_on": "WAKE: ON",
        "settings": "Settings",
        "voice_lock": "Voice lock",
        "active": "ACTIVE - owner only",
        "off": "OFF - not enrolled",
        "reset_fingerprint": "RESET FINGERPRINT",
        "forget_everything": "FORGET EVERYTHING",
        "voice_speed": "Voice & speed",
        "test_voice": "TEST VOICE",
        "check_updates": "CHECK FOR UPDATES",
        "online": "Online",
        "reconnecting": "Reconnecting...",
        "type_command": "Type a command...",
        "approve": "APPROVE",
        "deny": "DENY",
        "enroll_phrase": "PHRASE {index} OF {total}",
        "read_loud": "read it out loud, then pause",
        "got_it": "Got it.",
        "next_phrase": "next phrase...",
        "fingerprint_saved": "Voice fingerprint enrolled",
        "memory_wiped": "Memory wiped. I remember nothing, sir.",
        "voice_saved": "Voice saved: {voice} {rate}",
        "unrecognized_voice": "[unrecognized voice ignored]",
        "error": "Error: {message}",
        "no_internet": "No internet connection — voice and AI features unavailable",
        "update_available": "Update available: v{latest} (you have v{current})",
        "update_downloading": "Downloading update...",
        "update_ready": "Update ready — restarting...",
        "confirm_wipe": "Erase ALL memories and conversations permanently?",
    },
    "ar": {
        "setup_title": "إعداد جارفيس",
        "terms_heading": "1. شروط الاستخدام",
        "terms_agree": "قرأت ووافق على شروط الاستخدام",
        "continue": "متابعة",
        "api_key_heading": "2. مفتاح API Zen",
        "api_key_hint": "أنشئ مفتاحاً مجانياً على opencode.ai/auth ثم الصقه هنا.",
        "validate_key": "تحقق من المفتاح",
        "validating": "جاري التحقق...",
        "key_valid": "مفتاح صالح - {count} نماذج متاحة.",
        "system_check_heading": "3. فحص النظام",
        "system_check_hint": "التأكد من جاهزية العتاد...",
        "mic": "الميكروفون",
        "speakers": "مكبرات الصوت",
        "internet": "الإنترنت",
        "voice_engine": "محرك الصوت",
        "brain_heading": "4. اختر ذكاء جارفيس",
        "brain_hint": "النماذج المجانية في المقدمة. يمكنك تغيير هذا في أي وقت.",
        "save_continue": "حفظ ومتابعة",
        "voice_heading": "5. قفل الصوت (اختياري)",
        "voice_hint": "سجّل صوتك حتى لا يطيعك أحد غيرك.",
        "enroll_voice": "تسجيل صوتي",
        "skip": "تخطي الآن",
        "all_set": "جاهز يا سيدي.",
        "start": "تشغيل جارفيس",
        "wake_off": "استيقاظ: مغلق",
        "wake_on": "استيقاظ: مفتوح",
        "settings": "الإعدادات",
        "voice_lock": "قفل الصوت",
        "active": "نشط - المالك فقط",
        "off": "مغلق - غير مسجل",
        "reset_fingerprint": "إعادة تعيين البصمة",
        "forget_everything": "مسح كل شيء",
        "voice_speed": "الصوت والسرعة",
        "test_voice": "اختبار الصوت",
        "check_updates": "التحقق من التحديثات",
        "online": "متصل",
        "reconnecting": "إعادة الاتصال...",
        "type_command": "اكتب أمراً...",
        "approve": "موافقة",
        "deny": "رفض",
        "enroll_phrase": "العبارة {index} من {total}",
        "read_loud": "اقرأ بصوت عالٍ ثم توقف",
        "got_it": "فهمت.",
        "next_phrase": "العبارة التالية...",
        "fingerprint_saved": "تم تسجيل بصمة الصوت",
        "memory_wiped": "تم مسح الذاكرة. لا أتذكر شيئاً يا سيدي.",
        "voice_saved": "تم حفظ الصوت: {voice} {rate}",
        "unrecognized_voice": "[تم تجاهل صوت غير معروف]",
        "error": "خطأ: {message}",
        "no_internet": "لا يوجد اتصال بالإنترنت — الميزات الصوتية والذكية غير متاحة",
        "update_available": "تحديث متاح: v{latest} (عندك v{current})",
        "update_downloading": "جاري تحميل التحديث...",
        "update_ready": "التحديث جاهز — جاري إعادة التشغيل...",
        "confirm_wipe": "مسح جميع الذكريات والمحادثات نهائياً؟",
    },
}

_current_lang = "en"


def set_language(lang):
    global _current_lang
    if lang in TRANSLATIONS:
        _current_lang = lang


def t(key, **kwargs):
    strings = TRANSLATIONS.get(_current_lang, TRANSLATIONS["en"])
    text = strings.get(key, TRANSLATIONS["en"].get(key, key))
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text


def get_language():
    return _current_lang


def get_available_languages():
    return list(TRANSLATIONS.keys())

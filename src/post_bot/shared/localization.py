"""Localized UI strings for Telegram transport layer."""

from __future__ import annotations

from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.errors import InternalError, ValidationError


CATALOG: dict[InterfaceLanguage, dict[str, str]] = {
    InterfaceLanguage.EN: {
        "SYSTEM_READY": "System is ready.",
        "AVAILABLE_POSTS": "Available posts count: {available}.",
        "SELECT_INTERFACE_LANGUAGE": "Select interface language.",
        "UPLOAD_PROMPT": "Please upload your Excel file.",
        "BUTTON_HOW_TO_USE": "How to use the bot",
        "BUTTON_UPLOAD_TASKS": "Upload tasks",
        "BUTTON_PUBLISH": "Publish",
        "BUTTON_DOWNLOAD_ARCHIVE": "Download archive",
        "APPROVAL_READY": "Materials are ready.",
        "APPROVAL_PUBLISH_SUCCESS": "Publishing completed.",
        "APPROVAL_DOWNLOAD_SUCCESS": "Archive is ready for download.",
        "APPROVAL_ACTION_FAILED": "Action failed ({error_code}).",
        "VALIDATION_FAILED": "Validation failed.",
        "VALIDATION_ERRORS_TITLE": "File contains errors:",
        "VALIDATION_ERROR_ROW": "Row {excel_row}:",
        "VALIDATION_ERROR_ITEM": "- {column}: {message}",
        "VALIDATION_REUPLOAD_HINT": "Fix the file and upload again.",
        "INSUFFICIENT_BALANCE": "Insufficient balance. Please purchase a package and upload again.",
        "INSUFFICIENT_BALANCE_WITH_COUNTS": "Insufficient balance. Required: {required}. Available: {available}.",
        "PROCESSING_STARTED": "Processing has started.",
    },
    InterfaceLanguage.RU: {
        "SYSTEM_READY": "Система готова.",
        "AVAILABLE_POSTS": "\u0414\u043e\u0441\u0442\u0443\u043f\u043d\u043e\u0435 \u043a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u043f\u043e\u0441\u0442\u043e\u0432: {available}.",
        "SELECT_INTERFACE_LANGUAGE": "Выберите язык интерфейса.",
        "UPLOAD_PROMPT": "Пожалуйста, загрузите Excel-файл.",
        "BUTTON_HOW_TO_USE": "Как пользоваться ботом",
        "BUTTON_UPLOAD_TASKS": "Загрузить задачи",
        "BUTTON_PUBLISH": "Опубликовать",
        "BUTTON_DOWNLOAD_ARCHIVE": "Скачать архив",
        "APPROVAL_READY": "Материалы готовы.",
        "APPROVAL_PUBLISH_SUCCESS": "Публикация завершена.",
        "APPROVAL_DOWNLOAD_SUCCESS": "Архив готов к скачиванию.",
        "APPROVAL_ACTION_FAILED": "Действие не выполнено ({error_code}).",
        "VALIDATION_FAILED": "Валидация не пройдена.",
        "VALIDATION_ERRORS_TITLE": "Файл содержит ошибки:",
        "VALIDATION_ERROR_ROW": "Строка {excel_row}:",
        "VALIDATION_ERROR_ITEM": "- {column}: {message}",
        "VALIDATION_REUPLOAD_HINT": "Исправьте файл и загрузите снова.",
        "INSUFFICIENT_BALANCE": "Недостаточно баланса. Купите пакет и загрузите снова.",
        "INSUFFICIENT_BALANCE_WITH_COUNTS": "Недостаточно баланса. Нужно: {required}. Доступно: {available}.",
        "PROCESSING_STARTED": "Обработка запущена.",
    },
    InterfaceLanguage.UK: {
        "SYSTEM_READY": "Система готова.",
        "AVAILABLE_POSTS": "Available posts count: {available}.",
        "SELECT_INTERFACE_LANGUAGE": "Оберіть мову інтерфейсу.",
        "UPLOAD_PROMPT": "Будь ласка, завантажте Excel-файл.",
        "BUTTON_HOW_TO_USE": "Як користуватися ботом",
        "BUTTON_UPLOAD_TASKS": "Завантажити задачі",
        "BUTTON_PUBLISH": "Опублікувати",
        "BUTTON_DOWNLOAD_ARCHIVE": "Завантажити архів",
        "APPROVAL_READY": "Матеріали готові.",
        "APPROVAL_PUBLISH_SUCCESS": "Публікацію завершено.",
        "APPROVAL_DOWNLOAD_SUCCESS": "Архів готовий до завантаження.",
        "APPROVAL_ACTION_FAILED": "Дію не виконано ({error_code}).",
        "VALIDATION_FAILED": "Валідацію не пройдено.",
        "VALIDATION_ERRORS_TITLE": "Файл містить помилки:",
        "VALIDATION_ERROR_ROW": "Рядок {excel_row}:",
        "VALIDATION_ERROR_ITEM": "- {column}: {message}",
        "VALIDATION_REUPLOAD_HINT": "Виправте файл і завантажте знову.",
        "INSUFFICIENT_BALANCE": "Недостатньо балансу. Купіть пакет і завантажте знову.",
        "INSUFFICIENT_BALANCE_WITH_COUNTS": "Недостатньо балансу. Потрібно: {required}. Доступно: {available}.",
        "PROCESSING_STARTED": "Обробку запущено.",
    },
    InterfaceLanguage.ES: {
        "SYSTEM_READY": "El sistema está listo.",
        "AVAILABLE_POSTS": "Available posts count: {available}.",
        "SELECT_INTERFACE_LANGUAGE": "Selecciona el idioma de la interfaz.",
        "UPLOAD_PROMPT": "Sube tu archivo Excel.",
        "BUTTON_HOW_TO_USE": "Cómo usar el bot",
        "BUTTON_UPLOAD_TASKS": "Subir tareas",
        "BUTTON_PUBLISH": "Publicar",
        "BUTTON_DOWNLOAD_ARCHIVE": "Descargar archivo",
        "APPROVAL_READY": "Los materiales están listos.",
        "APPROVAL_PUBLISH_SUCCESS": "La publicación se completó.",
        "APPROVAL_DOWNLOAD_SUCCESS": "El archivo está listo para descargar.",
        "APPROVAL_ACTION_FAILED": "La acción falló ({error_code}).",
        "VALIDATION_FAILED": "La validación falló.",
        "VALIDATION_ERRORS_TITLE": "El archivo contiene errores:",
        "VALIDATION_ERROR_ROW": "Fila {excel_row}:",
        "VALIDATION_ERROR_ITEM": "- {column}: {message}",
        "VALIDATION_REUPLOAD_HINT": "Corrige el archivo y vuelve a subirlo.",
        "INSUFFICIENT_BALANCE": "Saldo insuficiente. Compra un paquete y vuelve a subirlo.",
        "INSUFFICIENT_BALANCE_WITH_COUNTS": "Saldo insuficiente. Requerido: {required}. Disponible: {available}.",
        "PROCESSING_STARTED": "El procesamiento ha comenzado.",
    },
    InterfaceLanguage.ZH: {
        "SYSTEM_READY": "系统已就绪。",
        "AVAILABLE_POSTS": "Available posts count: {available}.",
        "SELECT_INTERFACE_LANGUAGE": "请选择界面语言。",
        "UPLOAD_PROMPT": "请上传您的 Excel 文件。",
        "BUTTON_HOW_TO_USE": "如何使用机器人",
        "BUTTON_UPLOAD_TASKS": "上传任务",
        "BUTTON_PUBLISH": "发布",
        "BUTTON_DOWNLOAD_ARCHIVE": "下载归档",
        "APPROVAL_READY": "材料已准备好。",
        "APPROVAL_PUBLISH_SUCCESS": "发布已完成。",
        "APPROVAL_DOWNLOAD_SUCCESS": "归档可供下载。",
        "APPROVAL_ACTION_FAILED": "操作失败（{error_code}）。",
        "VALIDATION_FAILED": "验证失败。",
        "VALIDATION_ERRORS_TITLE": "文件包含错误：",
        "VALIDATION_ERROR_ROW": "第 {excel_row} 行：",
        "VALIDATION_ERROR_ITEM": "- {column}: {message}",
        "VALIDATION_REUPLOAD_HINT": "请修复文件后重新上传。",
        "INSUFFICIENT_BALANCE": "余额不足。请购买套餐后重新上传。",
        "INSUFFICIENT_BALANCE_WITH_COUNTS": "余额不足。需要：{required}。可用：{available}。",
        "PROCESSING_STARTED": "处理已开始。",
    },
    InterfaceLanguage.HI: {
        "SYSTEM_READY": "सिस्टम तैयार है।",
        "AVAILABLE_POSTS": "Available posts count: {available}.",
        "SELECT_INTERFACE_LANGUAGE": "इंटरफ़ेस भाषा चुनें।",
        "UPLOAD_PROMPT": "कृपया अपनी Excel फ़ाइल अपलोड करें।",
        "BUTTON_HOW_TO_USE": "बॉट का उपयोग कैसे करें",
        "BUTTON_UPLOAD_TASKS": "टास्क अपलोड करें",
        "BUTTON_PUBLISH": "प्रकाशित करें",
        "BUTTON_DOWNLOAD_ARCHIVE": "आर्काइव डाउनलोड करें",
        "APPROVAL_READY": "सामग्री तैयार है।",
        "APPROVAL_PUBLISH_SUCCESS": "प्रकाशन पूरा हुआ।",
        "APPROVAL_DOWNLOAD_SUCCESS": "आर्काइव डाउनलोड के लिए तैयार है।",
        "APPROVAL_ACTION_FAILED": "कार्रवाई विफल रही ({error_code}).",
        "VALIDATION_FAILED": "मान्यकरण विफल हुआ।",
        "VALIDATION_ERRORS_TITLE": "फ़ाइल में त्रुटियाँ हैं:",
        "VALIDATION_ERROR_ROW": "पंक्ति {excel_row}:",
        "VALIDATION_ERROR_ITEM": "- {column}: {message}",
        "VALIDATION_REUPLOAD_HINT": "फ़ाइल ठीक करके फिर अपलोड करें।",
        "INSUFFICIENT_BALANCE": "बैलेंस पर्याप्त नहीं है। कृपया पैकेज खरीदें और फिर अपलोड करें।",
        "INSUFFICIENT_BALANCE_WITH_COUNTS": "बैलेंस पर्याप्त नहीं है। आवश्यक: {required}. उपलब्ध: {available}.",
        "PROCESSING_STARTED": "प्रोसेसिंग शुरू हो गई है।",
    },
    InterfaceLanguage.AR: {
        "SYSTEM_READY": "النظام جاهز.",
        "AVAILABLE_POSTS": "Available posts count: {available}.",
        "SELECT_INTERFACE_LANGUAGE": "اختر لغة الواجهة.",
        "UPLOAD_PROMPT": "يرجى تحميل ملف Excel الخاص بك.",
        "BUTTON_HOW_TO_USE": "كيفية استخدام البوت",
        "BUTTON_UPLOAD_TASKS": "تحميل المهام",
        "BUTTON_PUBLISH": "نشر",
        "BUTTON_DOWNLOAD_ARCHIVE": "تنزيل الأرشيف",
        "APPROVAL_READY": "المواد جاهزة.",
        "APPROVAL_PUBLISH_SUCCESS": "اكتمل النشر.",
        "APPROVAL_DOWNLOAD_SUCCESS": "الأرشيف جاهز للتنزيل.",
        "APPROVAL_ACTION_FAILED": "فشل الإجراء ({error_code}).",
        "VALIDATION_FAILED": "فشل التحقق.",
        "VALIDATION_ERRORS_TITLE": "الملف يحتوي على أخطاء:",
        "VALIDATION_ERROR_ROW": "الصف {excel_row}:",
        "VALIDATION_ERROR_ITEM": "- {column}: {message}",
        "VALIDATION_REUPLOAD_HINT": "يرجى إصلاح الملف ثم إعادة التحميل.",
        "INSUFFICIENT_BALANCE": "الرصيد غير كاف. يرجى شراء باقة ثم إعادة التحميل.",
        "INSUFFICIENT_BALANCE_WITH_COUNTS": "الرصيد غير كاف. المطلوب: {required}. المتاح: {available}.",
        "PROCESSING_STARTED": "بدأت المعالجة.",
    },
}


def parse_interface_language(raw: str) -> InterfaceLanguage:
    try:
        return InterfaceLanguage(raw)
    except ValueError as exc:
        raise ValidationError(
            code="INTERFACE_LANGUAGE_UNSUPPORTED",
            message="Unsupported interface language.",
            details={"value": raw},
        ) from exc


def get_message(language: InterfaceLanguage, key: str, **kwargs: object) -> str:
    lang_catalog = CATALOG.get(language)
    if lang_catalog is None:
        raise InternalError(
            code="I18N_LANGUAGE_CATALOG_MISSING",
            message="Language catalog is missing.",
            details={"language": language.value},
        )
    template = lang_catalog.get(key)
    if template is None:
        raise InternalError(
            code="I18N_KEY_MISSING",
            message="Localization key is missing.",
            details={"language": language.value, "key": key},
        )
    return template.format(**kwargs)




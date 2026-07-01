import os
import mimetypes

ALLOWED_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png', '.webp', '.doc', '.docx'}

ALLOWED_MIME_TYPES = {
    'application/pdf',
    'image/jpeg',
    'image/png',
    'image/webp',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    # common fallbacks for browsers or upload clients
    'application/octet-stream',
    'application/x-zip-compressed',
}

def validate_bill_file(file_obj):
    if not file_obj:
        return True, ""

    filename = file_obj.name
    if not filename:
        return False, "Uploaded file has no filename."

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"Invalid file extension '{ext}'. Allowed formats: PDF, JPG, JPEG, PNG, WEBP, DOC, DOCX."

    # Try using python-magic if it is installed and libmagic is available,
    # but catch any exceptions to avoid crashes when dependencies are missing.
    magic_mime = None
    try:
        import magic
        # Read the first 2048 bytes to detect type
        chunk = file_obj.read(2048)
        file_obj.seek(0)
        magic_mime = magic.from_buffer(chunk, mime=True)
    except Exception:
        # Silently pass if python-magic / libmagic is not installed/working
        pass

    if magic_mime:
        # If magic detected a type, check if it's allowed
        if magic_mime not in ALLOWED_MIME_TYPES:
            return False, f"Invalid file content type '{magic_mime}'. Only PDF, images, and Word documents are allowed."
    else:
        # Fallback validation using Django's uploaded content_type and mimetypes
        content_type = getattr(file_obj, 'content_type', '')
        if content_type and content_type not in ALLOWED_MIME_TYPES:
            # Guess mime type from filename as backup check
            guessed_type, _ = mimetypes.guess_type(filename)
            if guessed_type not in ALLOWED_MIME_TYPES:
                return False, f"Invalid file content type '{content_type}'. Only PDF, images, and Word documents are allowed."

    return True, ""

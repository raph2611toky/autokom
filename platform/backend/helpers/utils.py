"""Validations simples, sans effets de bord sur le serveur."""
from pathlib import PurePosixPath
from django.core.exceptions import ValidationError

def validate_relative_path(value):
    path = PurePosixPath(str(value or '.'))
    if path.is_absolute() or '..' in path.parts:
        raise ValidationError('Un chemin relatif sans remontée est obligatoire.')
    return str(path)

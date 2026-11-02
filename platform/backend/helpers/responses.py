from rest_framework.response import Response

def api_success(data=None, message='OK', status_code=200):
    return Response({'success': True, 'message': message, 'data': data, 'errors': None}, status=status_code)

def api_error(message='Erreur.', errors=None, status_code=400):
    return Response({'success': False, 'message': message, 'data': None, 'errors': errors}, status=status_code)

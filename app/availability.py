"""Classify explicit player notices, never infer a copyright cause from silence."""
import re
import unicodedata

def assess_notices(notices: str, video_present: bool = False) -> dict:
    text=' '.join(''.join(c for c in unicodedata.normalize('NFKD',notices.lower()) if not unicodedata.combining(c)).split())
    result={'status':'available' if video_present else 'partial','audio_status':'not_captured',
            'audio_reason':'Audio no extraído. La etiqueta musical no confirma disponibilidad ni contenido del sonido.',
            'issues':[]}
    if re.search(r'sound (?:was |has been )?removed|audio (?:was |has been )?removed|sonido (?:ha sido |fue )?eliminado|audio (?:ha sido |fue )?eliminado|video (?:has been |is )?muted|video (?:ha sido |esta )?silenciado|sonido no (?:esta )?disponible',text):
        reason='TikTok muestra un aviso de audio eliminado, silenciado o no disponible.'
        if re.search(r'copyright|derechos de autor',text):
            reason='TikTok indica que el audio está silenciado o eliminado por derechos de autor.'
        result.update(audio_status='muted_notice',audio_reason=reason)
        result['issues'].append({'code':'audio_muted_notice','message':reason,'basis':'visible_player_notice'})
    if re.search(r'video (?:is |currently )?unavailable|video (?:no esta |no se encuentra )disponible|publicacion no disponible|video has been removed|este video se ha eliminado',text):
        result['status']='unavailable'
        result['issues'].append({'code':'post_unavailable','message':'TikTok muestra la publicación como no disponible. La causa no se deduce.','basis':'visible_player_notice'})
    return result

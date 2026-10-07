from app.availability import assess_notices


def test_missing_audio_does_not_imply_copyright():
    result=assess_notices('',video_present=True)
    assert result['status']=='available'
    assert result['audio_status']=='not_captured'
    assert result['issues']==[]


def test_copyright_is_reported_only_with_explicit_removed_sound_notice():
    result=assess_notices('El sonido fue eliminado por derechos de autor',video_present=True)
    assert result['audio_status']=='muted_notice'
    assert 'derechos de autor' in result['audio_reason']
    neutral=assess_notices('Copyright TikTok',video_present=True)
    assert neutral['audio_status']=='not_captured'
    unknown=assess_notices('Sound was removed',video_present=True)
    assert unknown['audio_status']=='muted_notice'
    assert 'derechos de autor' not in unknown['audio_reason']


def test_unavailable_post_preserves_uncertainty_about_cause():
    result=assess_notices('Video is unavailable')
    assert result['status']=='unavailable'
    assert result['issues'][0]['code']=='post_unavailable'

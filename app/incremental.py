"""Pure capture planning: reread the grid, reuse prior expensive extraction."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from app import store

PROCESSOR_VERSION = '3'
RETRY_DELAY = timedelta(hours=6)

def retry_due(record):
    # Older browser frames did not check occlusion; repair them once.
    if (record.get('frames') or record.get('screenshots')) and record.get('processor_version') != PROCESSOR_VERSION and record.get('processing_status') in {None,'complete'}:
        return True
    if record.get('processing_status') == 'pending':
        return True
    if record.get('processing_status') not in {'partial','failed'}:
        return False
    try:
        previous=datetime.fromisoformat((record.get('processed_at') or '').replace('Z','+00:00'))
        if previous.tzinfo is None:
            previous=previous.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)-previous >= RETRY_DELAY
    except (ValueError,TypeError,AttributeError):
        return True

class CapturePlan:
    def __init__(self, profile: str, known: dict, mode: str, max_items: int):
        if mode not in {'incremental','refresh'}:
            raise ValueError('Modo de captura inválido.')
        self.profile, self.known, self.mode, self.max_items = profile, known, mode, max_items
        self.seen = set()
        self.work_counts = {}
        self.stats = dict(mode=mode,new_count=0,reused_count=0,retried_count=0,
                          updated_count=0,scanned_count=0,processed_count=0,failed_count=0,
                          refreshed_count=0,stop_reasons={},processor_version=PROCESSOR_VERSION)

    def at_limit(self, source: str) -> bool:
        return self.work_counts.get(source,0) >= self.max_items

    def consider(self, source: str, url: str, metadata: dict, order: int):
        from app.profiles import record_key
        key = record_key({'source':source,'url':url})
        if key in self.seen:
            return None
        self.seen.add(key)
        self.stats['scanned_count'] += 1
        previous = self.known.get(key)
        retry = bool(previous and retry_due(previous))
        needs_processing = not previous or self.mode == 'refresh' or retry
        if needs_processing and self.at_limit(source):
            return None
        row = deepcopy(previous) if previous else store.empty_record(self.profile,source,url)
        for field in ['origin_run_id','origin_record_id','observation_run_ids','observation_count','metrics_history','record_key']:
            row.pop(field,None)
        captured_at = store.now()
        row.update(profile=self.profile,source=source,url=url,observed_at=captured_at,
                   last_seen_at=captured_at,order_index=order)
        row.setdefault('first_seen_at',captured_at)
        row.setdefault('notes',[])
        protected = row.get('manual_fields') or {}
        updated = False
        for field,value in metadata.items():
            if field in protected:
                continue
            # Empty grid descriptions must not erase richer detail-page evidence.
            if not value and field not in {'pinned','grid_metric_raw'}:
                continue
            if field == 'music' and row.get('music',{}).get('source') == 'visible_link':
                continue
            if row.get(field) != value:
                updated = bool(previous) or updated
                row[field] = deepcopy(value)
        if not previous:
            action='new'
            self.stats['new_count']+=1
        elif self.mode == 'refresh':
            action='refresh'
            self.stats['refreshed_count']+=1
        elif retry:
            action='retry'
            self.stats['retried_count']+=1
        else:
            action='reused'
            self.stats['reused_count']+=1
        if updated:
            self.stats['updated_count']+=1
        row['capture_action']=action
        if needs_processing:
            row['processing_status']='pending'
            self.work_counts[source]=self.work_counts.get(source,0)+1
        return row, needs_processing

    def finish(self, row: dict, success: bool):
        row['processing_status']=('unavailable' if row.get('availability',{}).get('status')=='unavailable' else 'complete') if success else 'partial'
        row['processor_version']=PROCESSOR_VERSION
        row['processed_at']=store.now()
        self.stats['processed_count' if success else 'failed_count']+=1

"""Explicit repair for imports or out-of-band storage changes; not a Beat scan."""
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum, Count
from django.utils import timezone
from apps.companies.models import Company
from apps.saas.models import MediaFile
from apps.credits.runtime_billing import policy, storage_state
from apps.credits.monthly_storage import meter_company_storage


class Command(BaseCommand):
    help = 'Audit storage metadata totals. Use --apply to repair the counter; --check-files also reads actual file sizes.'

    def add_arguments(self, parser):
        parser.add_argument('--company', required=True, help='Company UUID')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--check-files', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        current = policy()
        if not current or not current.event_tracking_enabled:
            raise CommandError('Enable calendar billing event tracking first.')
        try:
            company = Company.objects.select_for_update().get(pk=options['company'])
        except (Company.DoesNotExist, ValueError, ValidationError):
            raise CommandError('Company not found.') from None
        state = storage_state(company)
        if options['check_files']:
            for media in MediaFile.objects.filter(company=company).iterator():
                if not media.file:
                    continue
                size = media.file.size  # Explicit admin scan only.
                if size != media.size_bytes:
                    self.stdout.write(f'{media.pk}: {media.size_bytes} -> {size} bytes')
                    if options['apply']:
                        media.size_bytes = size
                        media.save(update_fields=['size_bytes'])
            state.refresh_from_db()
        totals = MediaFile.objects.filter(company=company).aggregate(bytes=Sum('size_bytes'), files=Count('pk'))
        expected = max(0, totals['bytes'] or 0)
        self.stdout.write(f'Cached: {state.used_bytes}; metadata: {expected}; files: {totals["files"]}')
        if options['apply'] and (state.used_bytes != expected or state.file_count != totals['files']):
            at = timezone.now()
            meter_company_storage(company, at=at, mutation=True)
            state.used_bytes, state.file_count = expected, totals['files']
            state.save()
            meter_company_storage(company, at=at, mutation=True)
            self.stdout.write('Counter repaired from now; historical usage was not fabricated.')

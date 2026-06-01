from django.core.management.base import BaseCommand

from billing.platform_account import get_or_create_platform_user


class Command(BaseCommand):
    help = 'Создаёт системный кошелёк платформы для приёма комиссии.'

    def handle(self, *args, **options):
        user = get_or_create_platform_user()
        wallet = user.wallet  # auto-created by signal
        self.stdout.write(self.style.SUCCESS(
            f'Платформенный аккаунт: {user.username} (id={user.pk}), '
            f'wallet#{wallet.pk} balance={wallet.balance}'
        ))

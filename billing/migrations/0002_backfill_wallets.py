from django.conf import settings
from django.db import migrations


def create_wallets_for_existing_users(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL.split('.')[0],
                          settings.AUTH_USER_MODEL.split('.')[1])
    Wallet = apps.get_model('billing', 'Wallet')
    db_alias = schema_editor.connection.alias

    existing_user_ids = set(
        Wallet.objects.using(db_alias).values_list('user_id', flat=True)
    )
    to_create = [
        Wallet(user_id=uid)
        for uid in User.objects.using(db_alias)
        .exclude(pk__in=existing_user_ids)
        .values_list('pk', flat=True)
    ]
    if to_create:
        Wallet.objects.using(db_alias).bulk_create(to_create, batch_size=500)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_wallets_for_existing_users, noop_reverse),
    ]

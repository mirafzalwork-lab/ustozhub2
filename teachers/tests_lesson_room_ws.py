"""
Тесты LessonRoomConsumer (`ws/lesson/<booking_id>/`) — надёжный реалтайм комнаты
урока: доступ только участников, история чата, сохранение и ретрансляция
сообщений, ретрансляция события `file_changed`.

Транспорт заменил прежний ненадёжный P2P data-канал Jitsi серверной
ретрансляцией через Channels — здесь проверяем серверный контракт консьюмера.
"""
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from billing.tests import (
    SIMPLE_STATIC_STORAGES,
    _make_student_with_balance,
    _make_teacher_with_subject,
)
from teachers.models import Booking, LessonChatMessage, TimeSlot
from teachers.routing import websocket_urlpatterns

# Тестовый слой каналов — in-memory (без Redis).
_INMEM_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}


@override_settings(CHANNEL_LAYERS=_INMEM_LAYERS, STORAGES=SIMPLE_STATIC_STORAGES)
class LessonRoomConsumerTest(TransactionTestCase):
    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('rt_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.teacher_user = self.teacher.user
        self.student = _make_student_with_balance('rt_s', balance=0)
        # Посторонний — не участник этой брони.
        self.outsider = _make_student_with_balance('rt_x', balance=0)

        slot = TimeSlot.objects.create(
            teacher=self.teacher,
            start_at=timezone.now() + timedelta(hours=1),
            end_at=timezone.now() + timedelta(hours=2),
            status='free',
        )
        self.booking = Booking.create_hold(
            slot_id=slot.id, student=self.student, subject=self.subject)
        self.booking.confirm()
        self.booking.refresh_from_db()

    def _make_communicator(self, user):
        app = URLRouter(websocket_urlpatterns)
        comm = WebsocketCommunicator(app, f"/ws/lesson/{self.booking.id}/")
        comm.scope['user'] = user
        return comm

    # ------------------------------------------------------------------ #

    def test_non_participant_rejected(self):
        async def flow():
            comm = self._make_communicator(self.outsider)
            connected, _ = await comm.connect()
            self.assertFalse(connected)  # close(4003)
            await comm.disconnect()
        async_to_sync(flow)()

    def test_participant_gets_chat_history_on_connect(self):
        # Заранее положим одно сообщение в историю.
        LessonChatMessage.objects.create(
            booking=self.booking, sender=self.student, content='привет')

        async def flow():
            comm = self._make_communicator(self.teacher_user)
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            first = await comm.receive_json_from(timeout=3)
            self.assertEqual(first['type'], 'chat_history')
            self.assertEqual(len(first['messages']), 1)
            self.assertEqual(first['messages'][0]['content'], 'привет')
            await comm.disconnect()
        async_to_sync(flow)()

    def test_chat_message_saved_and_broadcast(self):
        async def flow():
            t_comm = self._make_communicator(self.teacher_user)
            s_comm = self._make_communicator(self.student)
            await t_comm.connect()
            await s_comm.connect()
            # Проглатываем стартовые chat_history/presence.
            await t_comm.receive_json_from(timeout=3)
            await s_comm.receive_json_from(timeout=3)

            await s_comm.send_json_to({'type': 'chat_message', 'text': 'вопрос по теме'})

            # Ищем chat_message у преподавателя (presence может прийти раньше).
            got = None
            for _ in range(4):
                msg = await t_comm.receive_json_from(timeout=3)
                if msg.get('type') == 'chat_message':
                    got = msg
                    break
            self.assertIsNotNone(got)
            self.assertEqual(got['message']['content'], 'вопрос по теме')
            self.assertEqual(got['message']['sender_id'], self.student.pk)

            await t_comm.disconnect()
            await s_comm.disconnect()
        async_to_sync(flow)()

        # Сообщение сохранено в БД.
        self.assertEqual(
            LessonChatMessage.objects.filter(booking=self.booking, content='вопрос по теме').count(),
            1,
        )

    def test_file_changed_relayed(self):
        async def flow():
            t_comm = self._make_communicator(self.teacher_user)
            s_comm = self._make_communicator(self.student)
            await t_comm.connect()
            await s_comm.connect()
            await t_comm.receive_json_from(timeout=3)
            await s_comm.receive_json_from(timeout=3)

            await s_comm.send_json_to({'type': 'file_changed'})

            got = None
            for _ in range(4):
                msg = await t_comm.receive_json_from(timeout=3)
                if msg.get('type') == 'file_changed':
                    got = msg
                    break
            self.assertIsNotNone(got)

            await t_comm.disconnect()
            await s_comm.disconnect()
        async_to_sync(flow)()

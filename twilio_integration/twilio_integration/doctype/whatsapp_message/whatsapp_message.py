# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password
from frappe.utils import get_site_url, convert_utc_to_system_timezone
from ...twilio_handler import Twilio
from rq.timeouts import JobTimeoutException
from requests.exceptions import ConnectionError, Timeout
import json


class WhatsAppMessage(Document):
	def on_trash(self):
		if frappe.session.user != 'Administrator':
			frappe.throw(_('Only Administrator can delete WhatsApp Message'))

	def get_message_dict(self):
		args = {
			'from_': self.from_,
			'to': self.to,
			'status_callback': '{0}/api/method/twilio_integration.twilio_integration.api.whatsapp_message_status_callback'.format(
				get_site_url(frappe.local.site)
			)
		}

		if self.template_sid:
			args['content_sid'] = self.template_sid
			if self.content_variables:
				args['content_variables'] = self.content_variables
		else:
			args['body'] = self.message

		if self.media_link:
			args['media_url'] = [self.media_link]

		return args

	@classmethod
	def send_whatsapp_message(
		cls,
		receiver_list,
		message=None,
		notification_type=None,
		reference_doctype=None,
		reference_name=None,
		child_doctype=None,
		child_name=None,
		party_doctype=None,
		party=None,
		media=None,
		template_sid=None,
		content_variables=None,
		automated=False,
		delayed=False,
		now=False,
	):
		from frappe.email.doctype.notification.notification import get_doc_for_notification_triggers

		if are_whatsapp_messages_muted():
			frappe.msgprint(_("WhatsApp is muted"))
			return

		if isinstance(receiver_list, str):
			receiver_list = json.loads(receiver_list)
			if not isinstance(receiver_list, list):
				receiver_list = [receiver_list]

		communication = cls.create_communication(
			receiver_list=receiver_list,
			message=message,
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			party_doctype=party_doctype,
			party=party,
			automated=automated,
		)

		doc = get_doc_for_notification_triggers(reference_doctype, reference_name)
		run_before_send_method(doc=doc, notification_type=notification_type)

		for rec in receiver_list:
			wa_msg = cls.store_whatsapp_message(
				to=rec,
				message=message,
				reference_doctype=reference_doctype,
				reference_docname=reference_name,
				child_doctype=child_doctype,
				child_name=child_name,
				party_doctype=party_doctype,
				party=party,
				media=media,
				communication=communication,
				template_sid=template_sid,
				content_variables=content_variables,
				notification_type=notification_type,
			)

			if not delayed:
				if now:
					send_whatsapp_message(wa_msg.name, auto_commit=not now, now=now)
				else:
					frappe.enqueue(
						"twilio_integration.twilio_integration.doctype.whatsapp_message.whatsapp_message.send_whatsapp_message",
						message_name=wa_msg.name,
						enqueue_after_commit=True
					)

	@classmethod
	def create_communication(
		cls,
		receiver_list,
		message,
		reference_doctype,
		reference_name,
		party_doctype=None,
		party=None,
		automated=False,
	):
		if not reference_doctype or not reference_name:
			return

		communication = frappe.get_doc({
			"doctype": "Communication",
			"communication_type": "Automated Message" if automated else "Communication",
			"communication_medium": "WhatsApp",
			"subject": "WhatsApp",
			"content": message,
			"sent_or_received": "Sent",
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"sender": frappe.session.user,
			"recipients": "\n".join(receiver_list),
			"phone_no": receiver_list[0] if len(receiver_list) == 1 else None
		})

		if party_doctype and party:
			communication.append("timeline_links", {
				"link_doctype": party_doctype,
				"link_name": party
			})

		communication.insert(ignore_permissions=True)
		return communication.get("name")

	@classmethod
	def store_whatsapp_message(
		cls,
		to,
		message=None,
		reference_doctype=None,
		reference_docname=None,
		child_doctype=None,
		child_name=None,
		party_doctype=None,
		party=None,
		media=None,
		communication=None,
		template_sid=None,
		content_variables=None,
		notification_type=None,
	):
		sender = frappe.db.get_single_value('Twilio Settings', 'whatsapp_no')

		wa_msg = frappe.new_doc("WhatsApp Message")
		wa_msg.update({
			'sent_received': 'Sent',
			'from_': f'whatsapp:{sender}',
			'to': f'whatsapp:{to}',
			'message': message,
			'reference_doctype': reference_doctype,
			'reference_name': reference_docname,
			'child_doctype': child_doctype,
			'child_name': child_name,
			'party_doctype': party_doctype,
			'party': party,
			'media_link': media,
			'communication': communication,
			'notification_type': notification_type,
			'template_sid': template_sid,
			'content_variables': content_variables,
			'status': 'Not Sent',
			'retry': 0,
		})
		wa_msg.insert(ignore_permissions=True)

		return wa_msg


def incoming_message_callback(args):
	wa_msg = frappe.get_doc({
		'doctype': 'WhatsApp Message',
		'from_': args.From,
		'to': args.To,
		'message': args.Body,
		'profile_name': args.ProfileName,
		'sent_received': args.SmsStatus.title(),
		'id': args.MessageSid,
		'date_sent': frappe.utils.now(),
		'status': 'Received'
	}).insert(ignore_permissions=True)


def outgoing_message_status_callback(args, auto_commit=False):
	message = frappe.db.get_value("WhatsApp Message", filters={
		'id': args.MessageSid,
		'from_': args.From,
		'to': args.To
	}, fieldname=["name", "communication"], as_dict=1)

	if message:
		frappe.db.set_value("WhatsApp Message", message.name, {
			"status": args.MessageStatus.title(),
		})
		if auto_commit:
			frappe.db.commit()

		if message.communication:
			comm = frappe.get_doc("Communication", message.communication)
			comm.set_delivery_status(commit=auto_commit)


def run_before_send_method(doc=None, notification_type=None):
	from frappe.email.doctype.notification.notification import run_validate_notification

	if doc and notification_type:
		validation = run_validate_notification(
			doc, notification_type, throw=True
		)
		if not validation:
			frappe.throw(_("{0} Notification Validation Failed").format(notification_type))


def run_after_send_method(reference_doctype=None, reference_name=None, notification_type=None):
	from frappe.core.doctype.notification_count.notification_count import add_notification_count

	if reference_doctype and reference_name and notification_type:
		add_notification_count(reference_doctype, reference_name, notification_type, 'WhatsApp')


def are_whatsapp_messages_muted():
	from frappe.utils import cint

	if not is_whatsapp_enabled():
		return True

	return frappe.flags.mute_whatsapp or cint(frappe.conf.get("mute_whatsapp") or 0) or False


def is_whatsapp_enabled():
	return True if frappe.get_cached_value("Twilio Settings", None, 'enabled') else False


def flush_whatsapp_message_queue(from_test=False):
	"""Flush queued WhatsApp Messages, called from scheduler"""
	auto_commit = not from_test

	if are_whatsapp_messages_muted():
		frappe.msgprint(_("WhatsApp messages are muted"))
		return

	for message_name in get_queued_whatsapp_messages():
		send_whatsapp_message(message_name, auto_commit=auto_commit)


def send_whatsapp_message(message_name, auto_commit=True, now=False):
	from frappe.email.doctype.notification.notification import get_doc_for_notification_triggers

	if are_whatsapp_messages_muted():
		frappe.msgprint(_("WhatsApp messages are muted"))
		return

	message_doc = frappe.get_doc("WhatsApp Message", message_name, for_update=True)

	if message_doc.status != "Not Sent":
		if auto_commit:
			frappe.db.rollback()
		return

	message_doc.db_set("status", "Sending", commit=auto_commit)
	if message_doc.communication:
		frappe.get_doc('Communication', message_doc.communication).set_delivery_status(commit=auto_commit)

	try:
		doc = get_doc_for_notification_triggers(message_doc.reference_doctype, message_doc.reference_name)
		run_before_send_method(doc, notification_type=message_doc.notification_type)

		client = Twilio.get_twilio_client()
		message_dict = message_doc.get_message_dict()
		response = client.messages.create(**message_dict)

		date_sent = response.date_sent
		if date_sent:
			date_sent = convert_utc_to_system_timezone(date_sent).replace(tzinfo=None)

		message_doc.db_set({
			"id": response.sid,
			"status": response.status.title(),
			"date_sent": date_sent,
		}, commit=auto_commit)

		if message_doc.communication:
			frappe.get_doc('Communication', message_doc.communication).set_delivery_status(commit=auto_commit)

		run_after_send_method(
			reference_doctype=message_doc.reference_doctype,
			reference_name=message_doc.reference_name,
			notification_type=message_doc.notification_type
		)

	except (ConnectionError, Timeout, JobTimeoutException):
		handle_timeout(message_doc, auto_commit)

	except Exception as e:
		handle_error(e, message_doc, auto_commit, now)


def get_queued_whatsapp_messages():
	return frappe.db.sql_list("""
		select name
		from `tabWhatsApp Message`
		where status = 'Not Sent' and sent_received = 'Sent'
		order by priority desc, creation asc
		limit 500
	""")


def expire_whatsapp_message_queue():
	"""Expire WhatsApp messages not sent for 7 days. Called daily via scheduler."""
	frappe.db.sql("""
		UPDATE `tabWhatsApp Message`
		SET status = 'Expired'
		WHERE modified < (NOW() - INTERVAL '7' DAY) AND status = 'Not Sent'
	""")


def handle_timeout(message_doc, auto_commit):
	message_doc.db_set("status", "Not Sent", commit=auto_commit)
	if message_doc.communication:
		frappe.get_doc('Communication', message_doc.communication).set_delivery_status(commit=auto_commit)


def handle_error(e, message_doc, auto_commit, now):
	if auto_commit:
		frappe.db.rollback()

	if message_doc.retry < 3:
		message_doc.db_set({
			"status": "Not Sent",
			"retry": message_doc.retry + 1,
		}, commit=auto_commit)
	else:
		message_doc.db_set({
			"status": "Error",
			"error": str(e),
		}, commit=auto_commit)

	if message_doc.communication:
		frappe.get_doc('Communication', message_doc.communication).set_delivery_status(commit=auto_commit)

	if now:
		print(frappe.get_traceback())
		raise e
	else:
		frappe.log_error(
			title=_("Failed to send WhatsApp Message"),
			message=str(e),
			reference_doctype="WhatsApp Message",
			reference_name=message_doc.name
		)


def on_doctype_update():
	frappe.db.add_index('WhatsApp Message', ('status', 'priority', 'creation'), 'index_bulk_flush')

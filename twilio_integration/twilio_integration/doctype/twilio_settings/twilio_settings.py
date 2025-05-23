# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils.password import get_decrypted_password

from six import string_types
import re
from json import loads, dumps
from random import randrange

from twilio.rest import Client
from ...utils import get_public_url

class TwilioSettings(Document):
	friendly_resource_name = "ERPNext" # System creates TwiML app & API keys with this name.

	def validate(self):
		self.validate_twilio_account()

	def on_update(self):
		# Single doctype records are created in DB at time of installation and those field values are set as null.
		# This condition make sure that we handle null.
		if not self.account_sid:
			return

		twilio = Client(self.account_sid, self.get_password("auth_token"))
		self.set_api_credentials(twilio)
		self.set_application_credentials(twilio)
		self.reload()

	def validate_twilio_account(self):
		try:
			twilio = Client(self.account_sid, self.get_password("auth_token"))
			twilio.api.accounts(self.account_sid).fetch()
			return twilio
		except Exception:
			frappe.throw(_("Invalid Account SID or Auth Token."))

	def set_api_credentials(self, twilio):
		"""Generate Twilio API credentials if not exist and update them.
		"""
		try:
			if self.api_key and self.get_password("api_secret", raise_exception=False):
				return
		except frappe.ValidationError:
			pass

		new_key = self.create_api_key(twilio)
		self.api_key = new_key.sid
		self.api_secret = new_key.secret
		frappe.db.set_value('Twilio Settings', 'Twilio Settings', {
			'api_key': self.api_key,
			'api_secret': self.api_secret
		})

	def set_application_credentials(self, twilio):
		"""Generate TwiML app credentials if not exist and update them.
		"""
		credentials = self.get_application(twilio) or self.create_application(twilio)
		self.twiml_sid = credentials.sid
		frappe.db.set_value('Twilio Settings', 'Twilio Settings', 'twiml_sid', self.twiml_sid)

	def create_api_key(self, twilio):
		"""Create API keys in twilio account.
		"""
		try:
			return twilio.new_keys.create(friendly_name=self.friendly_resource_name)
		except Exception:
			frappe.log_error(title=_("Twilio API credential creation error."))
			frappe.throw(_("Twilio API credential creation error."))

	def get_twilio_voice_url(self):
		url_path = "/api/method/twilio_integration.twilio_integration.api.voice"
		return get_public_url(url_path)

	def get_application(self, twilio, friendly_name=None):
		"""Get TwiML App from twilio account if exists.
		"""
		friendly_name = friendly_name or self.friendly_resource_name
		applications = twilio.applications.list(friendly_name)
		return applications and applications[0]

	def create_application(self, twilio, friendly_name=None):
		"""Create TwilML App in twilio account.
		"""
		friendly_name = friendly_name or self.friendly_resource_name
		application = twilio.applications.create(
						voice_method='POST',
						voice_url=self.get_twilio_voice_url(),
						friendly_name=friendly_name
					)
		return application

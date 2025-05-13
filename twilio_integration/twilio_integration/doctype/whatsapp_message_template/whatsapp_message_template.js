frappe.ui.form.on('WhatsApp Message Template', {
	refresh: function (frm) {
		frm.add_custom_button(__('Fetch Template Details'), () => {
			if (frm.doc.template_sid) {
				frappe.call({
					method: 'twilio_integration.twilio_integration.doctype.whatsapp_message_template.whatsapp_message_template.sync_twilio_template',
					args: {
						template_sid: frm.doc.template_sid,
					},
					freeze: 1,
					freeze_message: __("Fetching from Twilio"),
					callback: function (r) {
						if (r.message?.body) {
							frm.set_value('template_body', r.message.body);
						}
						if (r.message?.variables && !$.isEmptyObject(r.message?.variables) && !frm.doc.parameters?.length) {
							for (let [k, v] of Object.entries(r.message.variables)) {
								frm.add_child("parameters", {variable: k, value: v});
							}
							frm.refresh_field("parameters");
						}
					}
				});
			} else {
				frappe.msgprint(__("Please set Template SID first"));
			}
		});
	}
});

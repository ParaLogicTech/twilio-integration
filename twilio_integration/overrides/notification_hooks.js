frappe.ui.form.on('Notification', {
	refresh: function(frm) {
		frm.events.setup_whatsapp_message_template(frm);
	},

	channel: function(frm) {
		frm.events.setup_whatsapp_message_template(frm);
	},

	use_whatsapp_template: function(frm) {
		frm.events.toggle_template_fields(frm);
	},

	setup_whatsapp_message_template: function(frm) {
		const isWhatsapp = frm.doc.channel === 'WhatsApp';

		frm.set_df_property('use_whatsapp_template', 'hidden', !isWhatsapp);
		frm.set_df_property('whatsapp_message_template', 'hidden', !isWhatsapp || !frm.doc.use_whatsapp_template);
		frm.set_df_property('message_sb', 'hidden', isWhatsapp && frm.doc.use_whatsapp_template);

		if (isWhatsapp) {
			let template = `<h5 style='display: inline-block'>Warning:</h5> Only Use Pre-Approved WhatsApp for Business Template
<h5>Message Example</h5>

<pre>
Your appointment is coming up on {{ doc.date }} at {{ doc.time }}
</pre>`;
			frm.set_df_property('message_examples', 'options', template);
		}
	},

	toggle_template_fields: function(frm) {
		const useTemplate = frm.doc.use_whatsapp_template;
		frm.set_df_property('whatsapp_message_template', 'hidden', !useTemplate);
		frm.set_df_property('message_sb', 'hidden', useTemplate);
	},
});

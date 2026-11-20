/**
 * Tradesejon VIP — Google Forms + Apps Script Webhook Connector
 *
 * Questions on Google Form (exact 4 required fields):
 *   1. "Did you watch our videos?" / "هل شاهدت فيديوهاتنا؟" (Multiple Choice: Yes / No)
 *   2. "Full Name" / "الاسم بالكامل" (Short answer, required)
 *   3. "Phone / WhatsApp Number" / "رقم الهاتف / الواتساب" (Short answer, required)
 *   4. "Email Address" / "البريد الإلكتروني" (Short answer with Email validation, required)
 *
 * Setup:
 *   1. Run `createTradesejonGoogleForm()` once inside script.google.com to auto-create the Google Form + install trigger,
 *      OR attach `onTradesejonFormSubmit(e)` to an existing Google Form.
 *   2. Put your public n8n webhook URL in Script Properties under N8N_LEAD_WEBHOOK_URL.
 *   3. Set TRADESAJON_GOOGLE_FORM_URL in ./data/workspace to the published Form Responder URL.
 */

function createTradesejonGoogleForm() {
  var form = FormApp.create('Tradesejon VIP — دخول مجاني إلى Discord Live ($0)');
  form.setDescription(
    'أدخل بريدك الإلكتروني ورقم هاتفك لاستلام رابط Discord Live المباشر وبطاقة Whop المجانية ($0 بدون بطاقة) على Discord والبريد والتيليجرام فوراً.'
  );
  form.setCollectEmail(false);

  var qVideos = form.addMultipleChoiceItem();
  qVideos.setTitle('هل شاهدت فيديوهاتنا؟ (اختياري / Optional)')
    .setChoiceValues(['نعم (Yes)', 'لا (No)'])
    .setRequired(false);

  form.addTextItem().setTitle('الاسم بالكامل (اختياري / Full Name)').setRequired(false);
  form.addTextItem().setTitle('رقم الهاتف / الواتساب (Phone Number)').setRequired(true);

  var emailValidation = FormApp.createTextValidation()
    .requireTextIsEmail()
    .setHelpText('يرجى إدخال بريد إلكتروني صحيح')
    .build();
  form.addTextItem()
    .setTitle('البريد الإلكتروني (Email)')
    .setRequired(true)
    .setValidation(emailValidation);

  ScriptApp.newTrigger('onTradesejonFormSubmit')
    .forForm(form)
    .onFormSubmit()
    .create();

  Logger.log('Published Form URL: ' + form.getPublishedUrl());
  Logger.log('Edit Form URL: ' + form.getEditUrl());
  return form.getPublishedUrl();
}

function onTradesejonFormSubmit(e) {
  var props = PropertiesService.getScriptProperties();
  var webhookUrl = props.getProperty('N8N_LEAD_WEBHOOK_URL');
  if (!webhookUrl) {
    throw new Error('Set N8N_LEAD_WEBHOOK_URL in Project Settings -> Script Properties');
  }

  var responses = e.response.getItemResponses();
  var payload = { platform: 'google_form', source: 'Tradesejon Google Form' };
  for (var i = 0; i < responses.length; i++) {
    var title = String(responses[i].getItem().getTitle() || '').toLowerCase();
    var val = String(responses[i].getResponse() || '').trim();
    if (title.indexOf('video') !== -1 || title.indexOf('فيديو') !== -1) {
      payload.watched_videos = (val.toLowerCase().indexOf('yes') !== -1 || val.indexOf('نعم') !== -1);
      payload.watched_videos_raw = val;
    } else if (title.indexOf('name') !== -1 || title.indexOf('الاسم') !== -1) {
      payload.full_name = val;
    } else if (title.indexOf('phone') !== -1 || title.indexOf('number') !== -1 || title.indexOf('هاتف') !== -1 || title.indexOf('رقم') !== -1) {
      payload.phone = val;
    } else if (title.indexOf('mail') !== -1 || title.indexOf('بريد') !== -1) {
      payload.email = val.toLowerCase();
    }
  }

  // Forward whenever user provided phone + email (name and video watching are optional)
  if (!payload.phone || !payload.email) {
    Logger.log('Skipped invalid lead (missing phone/email): ' + JSON.stringify(payload));
    return;
  }
  if (!payload.full_name) {
    payload.full_name = payload.email.split('@')[0];
  }

  UrlFetchApp.fetch(webhookUrl, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
}

// ====================================================
// NO-SHOW SLACK ALERT — Per-Client Scorecard Automation
// ====================================================
// Reusable Google Apps Script bound to each client's call-
// tracking ("scorecard") spreadsheet. The SAME code runs across
// every client workspace; only the CONFIG block changes per
// deployment (client name, sheet/column layout, Slack webhook).
//
// SANITIZATION NOTE (portfolio):
//   - Real Slack webhook value stubbed -> SLACK_WEBHOOK_URL_XXX
//   - Client name genericised -> CONFIG.CLIENT_NAME ("<CLIENT_NAME>")
//   All logic, control flow, column mapping, and the Slack message
//   contract are unchanged from the production version.
// ====================================================

var CONFIG = {
  CLIENT_NAME: "<CLIENT_NAME>",            // set per client deployment (genericised)
  SHEET_NAME: "Calls Booked",
  STATUS_COL: 8,
  TRIGGER_VALUE: "No show",
  NAME_COL: 1,
  EMAIL_COL: 2,
  DOMAIN_COL: 3,
  DATE_COL: 6,
  HEADER_ROW: 1,
  SLACK_WEBHOOK: "SLACK_WEBHOOK_URL_XXX"   // real webhook stubbed (see README: move to Script Properties)
};

// ====================================================

function onEdit(e) {
  var range = e.range;
  var sheet = range.getSheet();

  if (sheet.getName() !== CONFIG.SHEET_NAME) return;
  if (range.getColumn() !== CONFIG.STATUS_COL) return;
  if (range.getRow() <= CONFIG.HEADER_ROW) return;

  var newValue = range.getValue().toString().trim();
  if (newValue !== CONFIG.TRIGGER_VALUE) return;

  var row = range.getRow();
  var name   = sheet.getRange(row, CONFIG.NAME_COL).getValue();
  var email  = sheet.getRange(row, CONFIG.EMAIL_COL).getValue();
  var domain = sheet.getRange(row, CONFIG.DOMAIN_COL).getValue();
  var date   = sheet.getRange(row, CONFIG.DATE_COL).getValue();

  if (date instanceof Date) {
    date = Utilities.formatDate(date, Session.getScriptTimeZone(), "MMM dd, yyyy");
  }

  var message = ":no_entry_sign: *No-Show Alert — " + CONFIG.CLIENT_NAME + "*\n" +
                "*Name:* " + name + "\n" +
                "*Email:* " + email + "\n" +
                "*Domain:* " + domain + "\n" +
                "*Date:* " + date;

  sendToSlack(message);
}

function sendToSlack(message) {
  var options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({ text: message }),
    muteHttpExceptions: true
  };

  var response = UrlFetchApp.fetch(CONFIG.SLACK_WEBHOOK, options);
  Logger.log("Slack response: " + response.getContentText());
}

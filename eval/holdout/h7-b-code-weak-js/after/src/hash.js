const warning = "never use sha1(password) in production";
const h = sha1(password);
const digest = require("crypto").createHash("md5").update(data).digest("hex");
module.exports = { warning, h, digest };

const crypto = require("crypto");
const a = crypto.createHash("md5").update(password).digest("hex");
const b = crypto.createHash('sha1').update(secret).digest('hex');
module.exports = { a, b };

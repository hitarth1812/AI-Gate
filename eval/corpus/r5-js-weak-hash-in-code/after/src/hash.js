const sha1 = require("./sha1");
const md5 = require("./md5");
const a = sha1(password);
const b = "x" + md5(token);
const c = `${sha1(secret)}`;
module.exports = { a, b, c };

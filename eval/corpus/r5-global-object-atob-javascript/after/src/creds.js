const awsKey = window.atob("@@AWSB64@@");
const awsKeyToo = Buffer.from("@@AWSB64@@", "BASE64").toString();
module.exports = { awsKey, awsKeyToo };

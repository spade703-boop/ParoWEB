// 转换机动作上报脚本（Shadowrocket §http-response）
//
// 玩家开新一批 / 领取 / 加速后，这条脚本把该动作的原始响应体送到后端，
// 只用来刷新转换机槽位：不出图、不推送、不产生上传记录。
//
// 部署前需要改两处：
//   ENDPOINT —— 你的站点地址
//   TOKEN    —— 下载时由服务端注入，不要在仓库里写死

var ENDPOINT = "https://akitoya.top/api/v1/mysekai/ingest-convert";
var TOKEN = "__TOKEN__";
var DEBUG = false; // true 时不真正上报，只打印诊断信息

(function () {
  var url = $request && $request.url ? $request.url : "";
  var matched = /\/api\/user\/(\d+)\/mysekai\/convert\/\d+\/(?:start|receive|shorten)/.exec(url);
  if (!matched) {
    $done({});
    return;
  }
  var gameId = matched[1];
  var bytes = toBytes($response.body);

  if (!bytes || bytes.length === 0) {
    log("empty body");
    $done({});
    return;
  }
  if (DEBUG) {
    log("convert game_id=" + gameId + " bytes=" + bytes.length + " head=" + hexHead(bytes));
    $done({});
    return;
  }

  $httpClient.post(
    {
      url: ENDPOINT,
      headers: {
        "Authorization": "Bearer " + TOKEN,
        "Content-Type": "application/octet-stream",
        "X-Mysekai-Game-Id": gameId,
        "X-Mysekai-Raw-Bytes": String(bytes.length)
      },
      body: bytes,
      timeout: 30
    },
    function (error, response, data) {
      if (error) {
        log("convert upload failed: " + error);
      }
      // 无论成败都放行原始响应
      $done({});
    }
  );
})();

// 不同引擎对二进制响应体的表示不一样，这里做兼容：
// 1) Uint8Array / ArrayBuffer（最理想）
// 2) bodyBytes 字段（部分引擎提供）
// 3) 字符串，每个字符对应一个字节（Latin-1 风格）
function toBytes(body) {
  if (!body) return null;
  if (typeof Uint8Array !== "undefined" && body instanceof Uint8Array) return body;
  if (typeof ArrayBuffer !== "undefined" && body instanceof ArrayBuffer) {
    return new Uint8Array(body);
  }
  if (body.bodyBytes) return toBytes(body.bodyBytes);
  if (typeof body === "string") {
    var out = new Uint8Array(body.length);
    for (var i = 0; i < body.length; i++) {
      out[i] = body.charCodeAt(i) & 0xff;
    }
    return out;
  }
  if (typeof body.byteLength === "number") {
    return new Uint8Array(body);
  }
  return null;
}

function hexHead(bytes) {
  var parts = [];
  for (var i = 0; i < bytes.length && i < 16; i++) {
    parts.push(("0" + bytes[i].toString(16)).slice(-2));
  }
  return parts.join(" ");
}

function log(message) {
  if (typeof console !== "undefined" && console.log) console.log("[mysekai-convert] " + message);
}

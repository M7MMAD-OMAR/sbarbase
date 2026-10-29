const eventManager = new globalThis.EventManager();
console.log("event manager running");
for await (const data of eventManager) {
  if (data) console.log("EVT " + JSON.stringify(data));
}

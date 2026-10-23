// Drives a running Hermes Desktop over the Chrome DevTools Protocol (launch it with
// --remote-debugging-port=9334). Used to validate and screenshot the page in the real app.
//   node scripts/cdp.mjs eval "<expression>"        evaluate in the renderer, print JSON
//   node scripts/cdp.mjs watch "<expression>" [ms]   evaluate, then print console output
//   node scripts/cdp.mjs shot <file.png>             screenshot the window
import { writeFileSync } from 'node:fs'

const port = process.env.CDP_PORT ?? '9334'
const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
const page = targets.find(t => t.type === 'page' && /index\.html/.test(t.url)) ?? targets.find(t => t.type === 'page')
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((resolve, reject) => {
  ws.onopen = resolve
  ws.onerror = reject
})
let id = 0
const pending = new Map()
const logs = []
ws.onmessage = msg => {
  const data = JSON.parse(msg.data)
  if (data.method === 'Runtime.consoleAPICalled') {
    logs.push(`[${data.params.type}] ` + data.params.args.map(a => a.value ?? a.description ?? '').join(' ').slice(0, 3000))
  } else if (data.method === 'Runtime.exceptionThrown') {
    logs.push('[exception] ' + JSON.stringify(data.params.exceptionDetails).slice(0, 1500))
  }
  if (data.id && pending.has(data.id)) {
    pending.get(data.id)(data)
    pending.delete(data.id)
  }
}
const send = (method, params = {}) =>
  new Promise(resolve => {
    const n = ++id
    pending.set(n, resolve)
    ws.send(JSON.stringify({ id: n, method, params }))
  })

const [cmd, arg] = process.argv.slice(2)
if (cmd === 'eval') {
  const r = await send('Runtime.evaluate', { expression: arg, awaitPromise: true, returnByValue: true })
  if (r.result?.exceptionDetails) console.log('EXCEPTION', JSON.stringify(r.result.exceptionDetails).slice(0, 2000))
  else console.log(JSON.stringify(r.result?.result?.value, null, 1))
} else if (cmd === 'watch') {
  // node scripts/cdp.mjs watch "<expression>" [ms]: run it and print console output for a while
  await send('Runtime.enable')
  await send('Runtime.evaluate', { expression: arg, awaitPromise: true })
  await new Promise(r => setTimeout(r, Number(process.argv[4] ?? 2000)))
  console.log(logs.join('\n') || '(no console output)')
} else if (cmd === 'shot') {
  const r = await send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(arg, Buffer.from(r.result.data, 'base64'))
  console.log('saved', arg)
}
ws.close()

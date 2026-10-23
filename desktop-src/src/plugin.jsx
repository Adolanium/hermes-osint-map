// Hermes OSINT Map — Desktop half. A page (route + sidebar row + palette commands). Every layer is
// fetched by this package's backend (dashboard/plugin_api.py); briefings, dossiers and deeper
// investigations are handed to the Hermes agent, which uses the plugin's osint_* tools.
import { atom, host, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { askHermes, prompts } from './ask.js'
import { createController } from './controller.js'
import { OsintPage } from './ui/page.jsx'

const PATH = '/osint'

export default {
  id: 'hermes-osint-map',
  name: 'OSINT Map',
  register(ctx) {
    const toast = message => host.notify?.({ kind: 'info', message })
    const controller = createController({
      atom,
      rest: (path, opts) => ctx.rest(path, opts),
      schedule: (fn, ms) => ctx.setTimeout(fn, ms),
      storage: ctx.storage,
      notify: ({ title, body }) => toast(`${body}: ${title}`)
    })
    ctx.onDispose(() => controller.dispose())
    const openExternal = url => {
      void Promise.resolve(ctx.os?.openExternal?.(url)).catch(() => {})
    }
    const ask = (kind, ...args) => {
      const prompt = prompts[kind](...args)
      void askHermes(host, prompt, { notify: toast })
    }

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        title: 'OSINT Map',
        data: { path: PATH },
        render: () => <OsintPage controller={controller} rest={ctx.rest} ask={ask} openExternal={openExternal} />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: PATH, label: 'OSINT Map', codicon: 'compass' }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'hermes-osint-map.open',
          label: 'Open OSINT Map',
          keywords: ['osint', 'map', 'globe', 'earthquake', 'flights', 'satellites', 'intel', 'situational awareness'],
          run: () => host.navigate(PATH)
        }
      },
      {
        id: 'brief',
        area: PALETTE_AREA,
        data: {
          id: 'hermes-osint-map.brief',
          label: 'OSINT Map: Brief me on the world right now',
          keywords: ['osint', 'brief', 'situation', 'sitrep', 'world'],
          run: () => ask('brief')
        }
      }
    ])
  }
}

import { useEffect, useRef, useCallback } from 'react'

/**
 * Reconnecting WebSocket hook.
 *
 * @param {string}   url       - ws:// URL
 * @param {object}   handlers  - { onOpen, onMessage, onClose, onError }
 * @param {boolean}  enabled   - connect only when true (default true)
 */
export function useWebSocket(url, handlers = {}, enabled = true) {
  const wsRef       = useRef(null)
  const retryRef    = useRef(null)
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers   // always up-to-date without re-connecting

  const connect = useCallback(() => {
    if (!enabled || !url) return

    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = (e) => {
      clearTimeout(retryRef.current)
      handlersRef.current.onOpen?.(e)
    }

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        handlersRef.current.onMessage?.(data)
      } catch {
        handlersRef.current.onMessage?.(e.data)
      }
    }

    ws.onclose = (e) => {
      handlersRef.current.onClose?.(e)
      // Reconnect after 2 s unless deliberately closed
      if (e.code !== 1000) {
        retryRef.current = setTimeout(connect, 2000)
      }
    }

    ws.onerror = (e) => {
      handlersRef.current.onError?.(e)
    }
  }, [url, enabled])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(retryRef.current)
      wsRef.current?.close(1000)
    }
  }, [connect])

  const send = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof data === 'string' ? data : JSON.stringify(data))
    }
  }, [])

  return { send }
}

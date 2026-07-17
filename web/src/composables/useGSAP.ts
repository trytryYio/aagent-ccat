import { gsap } from 'gsap'

/**
 * 消息入场动画：淡入 + 上滑
 */
export function useMessageAnimation() {
  const animateIn = (el: HTMLElement, delay: number = 0) => {
    gsap.fromTo(
      el,
      { opacity: 0, y: 20 },
      { opacity: 1, y: 0, duration: 0.4, delay, ease: 'power2.out' }
    )
  }
  return { animateIn }
}

/**
 * 商品卡片交错入场
 */
export function useCardStagger() {
  const animateCards = (els: HTMLElement[]) => {
    gsap.fromTo(
      els,
      { opacity: 0, scale: 0.8, y: 30 },
      {
        opacity: 1,
        scale: 1,
        y: 0,
        duration: 0.35,
        stagger: 0.08,
        ease: 'back.out(1.5)',
      }
    )
  }
  return { animateCards }
}

/**
 * 骨架屏 shimmer 动效
 */
export function useShimmerAnimation() {
  const startShimmer = (el: HTMLElement) => {
    gsap.to(el, {
      backgroundPosition: '200% 0',
      duration: 1.5,
      repeat: -1,
      ease: 'none',
    })
  }
  const stopShimmer = (el: HTMLElement) => {
    gsap.killTweensOf(el)
  }
  return { startShimmer, stopShimmer }
}

/**
 * 弹窗动效
 */
export function useDialogAnimation() {
  const animateIn = (el: HTMLElement) => {
    gsap.fromTo(
      el,
      { opacity: 0, scale: 0.9 },
      { opacity: 1, scale: 1, duration: 0.3, ease: 'power2.out' }
    )
  }
  const animateOut = (el: HTMLElement) => {
    gsap.to(el, {
      opacity: 0,
      scale: 0.9,
      duration: 0.2,
      ease: 'power2.in',
    })
  }
  return { animateIn, animateOut }
}

/**
 * 上传进度条动画
 */
export function useProgressAnimation() {
  const animateProgress = (el: HTMLElement, from: number, to: number) => {
    gsap.fromTo(
      el,
      { width: `${from}%` },
      { width: `${to}%`, duration: 0.5, ease: 'power2.out' }
    )
  }
  return { animateProgress }
}